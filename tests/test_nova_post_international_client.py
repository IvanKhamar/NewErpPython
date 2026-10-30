"""HTTP calls against a fake server (ported from the .NET and Java tests); the company and answers are invented."""

import asyncio
import json
from decimal import Decimal
from urllib.parse import unquote

import httpx
import pytest

from newerp.application.delivery.international import HsCodeDto
from newerp.application.delivery.models import CreatedShipment
from newerp.config import InternationalSender, NovaPostInternationalSettings
from newerp.domain.common import DomainError
from newerp.domain.international import InternationalShipment, Item, Recipient, Seat
from newerp.infrastructure.delivery.nova_post_international import HttpNovaPostInternationalClient

SETTINGS = NovaPostInternationalSettings(
    api_key="secret-key",
    base_url="https://npi.test/v.1.0",
    sender=InternationalSender(
        company_tin="12345678",
        company_name="ТОВ Тест",
        name="Іван Тестовий",
        phone="+380 50 000 00 00",
        email="shop@example.com",
        city="Київ",
        street="вул. Тестова",
        building="1",
        post_code="01001",
    ),  # fmt: skip
)
SHIPMENT = InternationalShipment.of(
    1001,
    Recipient(
        "Anna", "Kowalska", "48600100200", "anna@example.com", "PL", "", "Kraków", "Floriańska", "5", "2", "31-019"
    ),
    None,
    [Item("Сукня лляна", "Linen dress", "6204491000", 450, Decimal(1), Decimal(3000))],
    [Seat(Decimal(1), Decimal(31), Decimal(36), Decimal(6))],
    Decimal(3000),
    "UAH",
)


class FakeServer:
    def __init__(self, *responses: tuple[int, str]) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self._responses.pop(0)
        return httpx.Response(status, text=body)

    def client(self) -> HttpNovaPostInternationalClient:
        return HttpNovaPostInternationalClient(SETTINGS, httpx.MockTransport(self))

    def auth(self, index: int) -> str | None:
        return self.requests[index].headers.get("Authorization")

    def json(self, index: int) -> dict:
        return json.loads(self.requests[index].content)


def test_create_sends_the_jwt_as_is_and_the_old_sites_payload():
    server = FakeServer(
        (200, '{"jwt":"token-1"}'),
        (201, '{"id":"sh-77","number":"NP000123456UA","status":"ReadyToShip","cost":812.5}'),
    )

    created = asyncio.run(server.client().create(SHIPMENT))

    assert created == CreatedShipment(ref="sh-77", number="NP000123456UA", cost=Decimal("812.5"))
    assert server.requests[0].url.path == "/v.1.0/clients/authorization"
    assert server.requests[0].url.params["apiKey"] == "secret-key"
    assert server.requests[1].url.path == "/v.1.0/shipments"
    assert server.auth(1) == "token-1"
    body = server.json(1)
    assert (body["status"], body["clientOrder"], body["payerType"]) == ("ReadyToShip", "1001", "Sender")
    parcel = body["parcels"][0]
    assert (parcel["width"], parcel["length"], parcel["height"], parcel["actualWeight"]) == (310, 360, 60, 450)
    assert parcel["insuranceCost"] == 3000
    sender = body["sender"]
    assert (sender["phone"], sender["countryCode"]) == ("380500000000", "UA")
    assert sender["registrationAddressParts"]["postCode"] == "01001"
    recipient = body["recipient"]
    assert (recipient["name"], recipient["countryCode"], recipient["addressParts"]["flat"]) == (
        "Anna Kowalska",
        "PL",
        "2",
    )
    assert (body["invoice"]["incoterm"], body["invoice"]["items"][0]["hsCode"]) == ("DAP", "6204491000")


def test_takes_a_new_jwt_once_when_the_old_one_expired():
    server = FakeServer(
        (200, '{"jwt":"token-1"}'),
        (200, '{"items":[{"hsCode":"6204491000","product":{"currentLocal":"Сукня","en":"Dress"},'
              '"subCategory":{"currentLocal":"З льону"}}]}'),
        (401, ""),
        (200, '{"jwt":"token-2"}'),
        (200, '{"items":[]}'),
    )  # fmt: skip
    client = server.client()

    async def run():
        first = await client.search_hs_codes("dress", "PL")
        second = await client.search_hs_codes("dress", "PL")
        return first, second

    first, second = asyncio.run(run())

    assert first == [HsCodeDto(hs_code="6204491000", label="Сукня — З льону", name_eng="Dress")]
    assert second == []
    assert (server.auth(1), server.auth(2), server.auth(4)) == ("token-1", "token-1", "token-2")
    query = unquote(server.requests[4].url.query.decode())
    assert "keyword=dress" in query and "country-code=PL" in query


def test_api_errors_become_a_readable_message():
    server = FakeServer(
        (200, '{"jwt":"token-1"}'),
        (
            422,
            '{"errors":{"recipient.addressParts.postCode":"Invalid postcode","invoice.items.0.hsCode":"Unknown code"}}',
        ),
    )

    with pytest.raises(DomainError) as error:
        asyncio.run(server.client().create(SHIPMENT))

    assert str(error.value) == (
        "Nova Post International відмовила (HTTP 422): recipient.addressParts.postCode: Invalid postcode; "
        "invoice.items.0.hsCode: Unknown code"
    )


def test_a_refused_key_is_said_so_and_delete_uses_the_shipment_id():
    refused = FakeServer((401, ""))
    with pytest.raises(DomainError, match=r"не прийняла ключ API \(HTTP 401\)"):
        asyncio.run(refused.client().delete("sh-77"))

    server = FakeServer((200, '{"jwt":"token-1"}'), (200, ""))
    asyncio.run(server.client().delete("sh-77"))

    request = server.requests[1]
    assert (request.url.path, request.method, server.auth(1)) == ("/v.1.0/shipments/sh-77", "DELETE", "token-1")
