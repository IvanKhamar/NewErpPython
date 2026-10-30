"""The real Nova Poshta client without a network: httpx.MockTransport records requests and answers like the API."""

import asyncio
import json
from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest

from newerp.application.delivery.models import (
    CreatedShipment,
    NewShipment,
    ShipmentPrintKind,
    ShipmentSeat,
    TrackingRequest,
)
from newerp.config import NovaPoshtaDropoffPoint, NovaPoshtaSender, NovaPoshtaSettings
from newerp.domain.common import DomainError
from newerp.infrastructure.delivery.nova_poshta import HttpNovaPoshtaClient

SETTINGS = NovaPoshtaSettings(
    senders=[
        NovaPoshtaSender(
            id="fop-a",
            name="ФОП А",
            api_key="key-a",
            counterparty_ref="cp-a",
            contact_ref="contact-a",
            phone="380501112233",
        ),
        NovaPoshtaSender(
            id="fop-b",
            name="ФОП Б",
            api_key="key-b",
            counterparty_ref="cp-b",
            contact_ref="contact-b",
            phone="380504445566",
            is_default=True,
        ),
    ],  # fmt: skip
    dropoff_points=[
        NovaPoshtaDropoffPoint(
            id="kyiv", name="Київ, №5", city_ref="city-sender", address_ref="wh-sender", is_default=True
        )
    ],
)


class FakeApi:
    def __init__(self, *responses: str | httpx.Response) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self._responses.pop(0)
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, text=answer)

    def json(self, index: int) -> dict:
        return json.loads(self.requests[index].content)

    def client(self) -> HttpNovaPoshtaClient:
        return HttpNovaPoshtaClient(SETTINGS, httpx.MockTransport(self))


def shipment(**changes) -> NewShipment:
    values = dict(
        sender_id="fop-a", dropoff_point_id="kyiv", order_id=291, ship_date=date(2026, 10, 16), first_name="Тарас",
        last_name="Шевченко", middle_name="", phone="380671234567", city_ref="city-kyiv", warehouse_ref="wh-1",
        declared_cost=Decimal(1500), cash_on_delivery=Decimal(1200), sender_pays=False,
        seats=[ShipmentSeat(weight=Decimal("1.5"), width=31, length=36, height=6),
               ShipmentSeat(weight=2, width=41, length=48, height=8)],
    )  # fmt: skip
    return NewShipment(**(values | changes))


def test_track_sends_numbers_with_phones_and_parses_mixed_formats():
    api = FakeApi("""{"success": true, "data": [
        {"Number": "20450000000001", "Status": "Прибув у відділення", "StatusCode": "7", "RecipientAddress": "Київ, №1",
         "Redelivery": 1, "DateCreated": "15-10-2026 10:30:00", "ScheduledDeliveryDate": "2026-10-17 12:00:00",
         "DatePayedKeeping": "2026-10-22 00:00:00"},
        {"Number": "20450000000002", "StatusCode": 9}]}""")

    infos = asyncio.run(api.client().track([TrackingRequest("20450000000001", "380670000001")]))

    request = api.json(0)
    assert request["apiKey"] == "key-b"  # without a separate key: the default sender's
    assert request["calledMethod"] == "getStatusDocuments"
    assert request["methodProperties"]["Documents"][0]["Phone"] == "380670000001"
    assert (infos[0].status_code, infos[0].redelivery) == (7, True)
    assert infos[0].date_created == datetime(2026, 10, 15, 10, 30)
    assert infos[0].date_payed_keeping == date(2026, 10, 22)
    assert (infos[1].status_code, infos[1].status, infos[1].date_created) == (9, "", None)


def test_api_error_becomes_domain_error():
    api = FakeApi('{"success": false, "errors": ["API key expired"]}')

    with pytest.raises(DomainError, match="API key expired"):
        asyncio.run(api.client().track([TrackingRequest("20450000000001", "")]))


def test_create_saves_recipient_then_document_with_sender_key():
    api = FakeApi(
        '{"success": true, "data": [{"Ref": "recipient-ref", "ContactPerson": {"data": [{"Ref": "contact-ref"}]}}]}',
        '{"success": true, "data": [{"Ref": "doc-ref", "IntDocNumber": "20450123456789", "CostOnSite": 85}]}',
    )

    created = asyncio.run(api.client().create(shipment()))

    assert created == CreatedShipment(ref="doc-ref", number="20450123456789", cost=Decimal(85))
    counterparty = api.json(0)
    assert (counterparty["apiKey"], counterparty["modelName"], counterparty["calledMethod"]) == (
        "key-a",
        "Counterparty",
        "save",
    )
    assert counterparty["methodProperties"]["CounterpartyProperty"] == "Recipient"
    p = api.json(1)["methodProperties"]
    assert (p["Sender"], p["ContactSender"], p["SenderAddress"]) == ("cp-a", "contact-a", "wh-sender")
    assert (p["Recipient"], p["ContactRecipient"], p["RecipientAddress"]) == ("recipient-ref", "contact-ref", "wh-1")
    assert (p["PayerType"], p["DateTime"], p["SeatsAmount"], p["Weight"]) == ("Recipient", "16.10.2026", "2", "3.5")
    assert (p["AfterpaymentOnGoodsCost"], p["InfoRegClientBarcodes"]) == ("1200", "291")
    assert p["OptionsSeat"][0]["volumetricVolume"] == "0.006696"


def test_create_without_cash_on_delivery_does_not_send_afterpayment():
    api = FakeApi(
        '{"success": true, "data": [{"Ref": "r", "ContactPerson": {"data": [{"Ref": "c"}]}}]}',
        '{"success": true, "data": [{"Ref": "d", "IntDocNumber": "20450123456789", "CostOnSite": "70"}]}',
    )

    asyncio.run(api.client().create(shipment(sender_id="fop-b", cash_on_delivery=Decimal(0), sender_pays=True)))

    p = api.json(1)["methodProperties"]
    assert "AfterpaymentOnGoodsCost" not in p
    assert p["PayerType"] == "Sender"


def test_print_returns_pdf_and_rejects_anything_else():
    pdf = FakeApi(httpx.Response(200, content=b"%PDF-1.4 ..."))
    html = FakeApi(httpx.Response(200, text="<html>Помилка</html>"))

    content = asyncio.run(pdf.client().print("fop-a", "doc-ref", ShipmentPrintKind.MARKINGS))

    assert content.startswith(b"%PDF")
    assert "/printMarkings/orders[]/doc-ref/type/pdf/zebra/zebra/apiKey/key-a" in str(pdf.requests[0].url)
    with pytest.raises(DomainError):
        asyncio.run(html.client().print("fop-a", "doc-ref", ShipmentPrintKind.DOCUMENT))


def test_fetch_warehouses_reads_pages_until_empty():
    api = FakeApi(
        '{"success": true, "data": [{"Ref": "w1", "Description": "Відділення №1", "CategoryOfWarehouse": "Branch",'
        ' "CityRef": "c", "CityDescription": "Київ", "TotalMaxWeightAllowed": "30", "WarehouseStatus": "Working"}]}',
        '{"success": true, "data": [{"Ref": "w2", "Description": "Поштомат №2", "CategoryOfWarehouse": "Postomat",'
        ' "CityRef": "c", "CityDescription": "Київ", "TotalMaxWeightAllowed": 20, "WarehouseStatus": "Closed"}]}',
        '{"success": true, "data": []}',
    )

    warehouses = asyncio.run(api.client().fetch_warehouses())

    assert [(w.ref, w.sort_order, w.max_weight, w.status) for w in warehouses] == [
        ("w1", 1, 30, True),
        ("w2", 2, 20, False),
    ]
    assert [api.json(i)["methodProperties"]["Page"] for i in range(3)] == [1, 2, 3]
