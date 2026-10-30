"""The Nova Poshta JSON API 2.0 client and its stub."""

import random
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx

from newerp.application.delivery.models import (
    CreatedShipment,
    DropoffPointOption,
    NewShipment,
    SenderOption,
    ShipmentPrintKind,
    TrackingRequest,
)
from newerp.config import NovaPoshtaSender, NovaPoshtaSettings
from newerp.domain.common import DomainError, plain
from newerp.domain.delivery import NovaPoshtaWarehouse, TrackingInfo

WAREHOUSES_PAGE_SIZE = 500
MAX_WAREHOUSE_PAGES = 1000  # guard against an endless loop
DATE_FORMATS = ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y")
TIMEOUT_SECONDS = 30


class HttpNovaPoshtaClient:
    """
    The real client. Tested on answers in the documented format; not yet run on a real key. Requests are not
    logged, so the keys (in request bodies and print URLs) never reach a log.
    """

    def __init__(self, settings: NovaPoshtaSettings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        for sender in settings.senders:
            values = (
                sender.id,
                sender.api_key.get_secret_value(),
                sender.counterparty_ref,
                sender.contact_ref,
                sender.phone,
            )
            if any(not value.strip() for value in values):
                raise ValueError(
                    f'Відправник Нової Пошти "{sender.id}": потрібні Id, ApiKey, CounterpartyRef, ContactRef і Phone.'
                )
        if any(not (p.id and p.city_ref and p.address_ref) for p in settings.dropoff_points):
            raise ValueError("Місце відправки Нової Пошти: потрібні Id, CityRef і AddressRef.")
        self._settings = settings
        self._transport = transport  # tests pass httpx.MockTransport
        self._senders = [SenderOption(id=s.id, name=s.name, is_default=s.is_default) for s in settings.senders]
        self._points = [
            DropoffPointOption(id=p.id, name=p.name, is_default=p.is_default) for p in settings.dropoff_points
        ]

    @property
    def is_configured(self) -> bool:
        return self._settings.tracking_key is not None

    @property
    def can_create_documents(self) -> bool:
        return bool(self._senders and self._points)

    @property
    def senders(self) -> list[SenderOption]:
        return self._senders

    @property
    def dropoff_points(self) -> list[DropoffPointOption]:
        return self._points

    async def track(self, documents: list[TrackingRequest]) -> list[TrackingInfo]:
        if not documents:
            return []
        data = await self._call(
            self._settings.tracking_key,
            "TrackingDocument",
            "getStatusDocuments",
            {"Documents": [{"DocumentNumber": d.number, "Phone": d.phone} for d in documents]},
        )
        return [parse_tracking(item) for item in data]

    async def create(self, shipment: NewShipment) -> CreatedShipment:
        sender = self._sender(shipment.sender_id)
        point = next((p for p in self._settings.dropoff_points if p.id == shipment.dropoff_point_id), None)
        if point is None:
            raise DomainError(f"Невідоме місце відправки: {shipment.dropoff_point_id}.")
        key = sender.api_key.get_secret_value()

        # 1. The private-person recipient (an existing one is returned if it exists) and its contact person.
        recipient = (
            await self._call(
                key,
                "Counterparty",
                "save",
                {
                    "FirstName": shipment.first_name,
                    "MiddleName": shipment.middle_name,
                    "LastName": shipment.last_name,
                    "Phone": shipment.phone,
                    "Email": "",
                    "CounterpartyType": "PrivatePerson",
                    "CounterpartyProperty": "Recipient",
                    "CityRef": shipment.city_ref,
                },
            )  # fmt: skip
        )[0]
        recipient_ref = text(recipient, "Ref")
        contacts = (recipient.get("ContactPerson") or {}).get("data") or []
        contact_ref = text(contacts[0], "Ref") if contacts else ""
        if not recipient_ref or not contact_ref:
            raise DomainError("Нова Пошта не повернула отримувача чи його контактну особу.")

        # 2. The waybill itself: branch → branch, paid in cash, as on the old site.
        properties: dict[str, Any] = {
            "PayerType": "Sender" if shipment.sender_pays else "Recipient",
            "PaymentMethod": "Cash",
            "DateTime": shipment.ship_date.strftime("%d.%m.%Y"),
            "CargoType": "Cargo",
            "ServiceType": "WarehouseWarehouse",
            "Description": self._settings.cargo_description,
            "Cost": number(shipment.declared_cost),
            "SeatsAmount": str(len(shipment.seats)),
            "Weight": number(sum((s.weight for s in shipment.seats), Decimal(0))),
            "OptionsSeat": [
                {
                    "volumetricVolume": number(s.volume),
                    "volumetricWidth": number(s.width),
                    "volumetricLength": number(s.length),
                    "volumetricHeight": number(s.height),
                    "weight": number(s.weight),
                }  # fmt: skip
                for s in shipment.seats
            ],
            "InfoRegClientBarcodes": str(shipment.order_id),
            "Sender": sender.counterparty_ref,
            "ContactSender": sender.contact_ref,
            "SendersPhone": sender.phone,
            "CitySender": point.city_ref,
            "SenderAddress": point.address_ref,
            "Recipient": recipient_ref,
            "ContactRecipient": contact_ref,
            "RecipientsPhone": shipment.phone,
            "CityRecipient": shipment.city_ref,
            "RecipientAddress": shipment.warehouse_ref,
        }
        # Cash on delivery through "payment control", as on the old site.
        if shipment.cash_on_delivery > 0:
            properties["AfterpaymentOnGoodsCost"] = number(shipment.cash_on_delivery)
        document = (await self._call(key, "InternetDocument", "save", properties))[0]
        waybill = text(document, "IntDocNumber")
        if not waybill:
            raise DomainError("Нова Пошта не повернула номер ТТН.")
        return CreatedShipment(
            ref=text(document, "Ref"), number=waybill, cost=decimal_or_zero(text(document, "CostOnSite"))
        )

    async def delete(self, sender_id: str, document_ref: str) -> None:
        key = self._sender(sender_id).api_key.get_secret_value()
        await self._call(key, "InternetDocument", "delete", {"DocumentRefs": [document_ref]})

    async def print(self, sender_id: str, document_ref: str, kind: ShipmentPrintKind) -> bytes:
        method = "printDocument" if kind == ShipmentPrintKind.DOCUMENT else "printMarkings"
        key = self._sender(sender_id).api_key.get_secret_value()
        url = (
            f"{self._settings.print_base_url}orders/{method}/orders[]/{quote(document_ref, safe='')}"
            f"/type/pdf/zebra/zebra/apiKey/{key}"
        )
        async with self._http() as http:
            response = await http.get(url)
        pdf = response.content
        # On an error Nova Poshta returns a page or JSON instead of a PDF; a real PDF starts with "%PDF".
        if not response.is_success or not pdf.startswith(b"%PDF"):
            raise DomainError("Нова Пошта не віддала PDF для друку. Перевірте, чи ТТН не видалена.")
        return pdf

    async def fetch_warehouses(self) -> list[NovaPoshtaWarehouse]:
        result: list[NovaPoshtaWarehouse] = []
        for page in range(1, MAX_WAREHOUSE_PAGES + 1):
            data = await self._call(
                self._settings.tracking_key, "Address", "getWarehouses", {"Page": page, "Limit": WAREHOUSES_PAGE_SIZE}
            )
            if not data:
                break
            result += [parse_warehouse(item) for item in data]
        return result

    def _sender(self, sender_id: str) -> NovaPoshtaSender:
        sender = next((s for s in self._settings.senders if s.id == sender_id), None)
        if sender is None:
            raise DomainError(f'Відправника "{sender_id}" немає в налаштуваннях Нової Пошти.')
        return sender

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=self._transport)

    async def _call(self, api_key: str | None, model: str, method: str, properties: dict) -> list[dict]:
        """One API call. Nova Poshta's errors become DomainError, so the person sees their text (400)."""
        request = {"apiKey": api_key, "modelName": model, "calledMethod": method, "methodProperties": properties}
        async with self._http() as http:
            response = await http.post(self._settings.base_url, json=request)
        response.raise_for_status()
        body = response.json()
        if body.get("success") is not True:
            errors = body.get("errors") or []
            raise DomainError("Нова Пошта відмовила: " + ("; ".join(map(str, errors)) if errors else "без пояснення"))
        return body.get("data") or []


def parse_tracking(item: dict) -> TrackingInfo:
    """One tracking record. Fields come as strings or numbers, and dates in different formats."""
    code = text(item, "StatusCode")
    scheduled = parse_date(text(item, "ScheduledDeliveryDate"))
    keeping = parse_date(text(item, "DatePayedKeeping"))
    return TrackingInfo(
        number=text(item, "Number"),
        status=text(item, "Status"),
        status_code=int(code) if code.lstrip("-").isdigit() else 0,
        recipient_address=text(item, "RecipientAddress"),
        redelivery=text(item, "Redelivery") in ("1", "true"),
        date_created=parse_date(text(item, "DateCreated")),
        date_scheduled=scheduled.date() if scheduled else None,
        date_payed_keeping=keeping.date() if keeping else None,
    )


def parse_warehouse(item: dict) -> NovaPoshtaWarehouse:
    return NovaPoshtaWarehouse(
        ref=text(item, "Ref"),
        description=text(item, "Description"),
        category=text(item, "CategoryOfWarehouse"),
        city_ref=text(item, "CityRef"),
        city_description=text(item, "CityDescription"),
        max_weight=int(decimal_or_zero(text(item, "TotalMaxWeightAllowed"))),
        working=text(item, "WarehouseStatus") == "Working",
    )


def text(item: dict, name: str) -> str:
    value = item.get(name)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    return ""


def parse_date(value: str) -> datetime | None:
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue
    return None


def decimal_or_zero(value: str) -> Decimal:
    try:
        return Decimal(value.replace(",", "")) if value else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def number(value: Decimal) -> str:
    """Six digits, so the volume in m³ is not lost (31×36×6 cm = 0.006696)."""
    return plain(value, 6)


TEST_SENDER_ID = "test"
TEST_DROPOFF_POINT_ID = "test"


class StubNovaPoshtaClient:
    """
    Used when there is no key: asks no one. With fake_documents (development only) it "creates" test waybills
    starting with 00 from a test sender; there is nothing to print for them.
    """

    def __init__(self, fake_documents: bool = False) -> None:
        self._fake = fake_documents
        self._senders = (
            [SenderOption(id=TEST_SENDER_ID, name="Тестовий відправник (заглушка)", is_default=True)]
            if fake_documents
            else []
        )
        self._points = (
            [DropoffPointOption(id=TEST_DROPOFF_POINT_ID, name="Тестове відділення (заглушка)", is_default=True)]
            if fake_documents
            else []
        )

    is_configured = False

    @property
    def can_create_documents(self) -> bool:
        return self._fake

    @property
    def senders(self) -> list[SenderOption]:
        return self._senders

    @property
    def dropoff_points(self) -> list[DropoffPointOption]:
        return self._points

    async def track(self, documents: list[TrackingRequest]) -> list[TrackingInfo]:
        return []

    async def create(self, shipment: NewShipment) -> CreatedShipment:
        if not self._fake:
            raise DomainError("Ключ API Нової Пошти не задано, тож ТТН створити не можна.")
        waybill = "00" + f"{random.randrange(1_000_000_000_000):012d}"
        return CreatedShipment(ref=str(uuid.uuid4()), number=waybill, cost=Decimal(0))

    async def delete(self, sender_id: str, document_ref: str) -> None:
        return None

    async def print(self, sender_id: str, document_ref: str, kind: ShipmentPrintKind) -> bytes:
        raise DomainError("Друк недоступний: ключ API Нової Пошти не задано.")

    async def fetch_warehouses(self) -> list[NovaPoshtaWarehouse]:
        return []


def nova_poshta_client(settings: NovaPoshtaSettings) -> HttpNovaPoshtaClient | StubNovaPoshtaClient:
    """The real client only with a key; without one the stub, so local development never reaches the service."""
    if settings.tracking_key is None:
        return StubNovaPoshtaClient(settings.fake_documents)
    return HttpNovaPoshtaClient(settings)
