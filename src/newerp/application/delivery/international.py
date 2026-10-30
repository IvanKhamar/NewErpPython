"""Waybills abroad through Nova Post International, like the "International" tab of the old site's waybill dialog.

The document lies with the domestic ones (dd_novaposhta_document) with carrier = international; it is deleted through
ShipmentService. The same as the Java version.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from newerp.application.common import Clock, Dto, Money, NotFoundError, ukrainian_key
from newerp.application.delivery.countries import COUNTRIES
from newerp.application.delivery.models import SEAT_PRESETS, CreatedShipment, SeatPreset, ShipmentDto, ShipmentSeat
from newerp.application.delivery.ports import ShipmentRepository
from newerp.domain.common import DomainError
from newerp.domain.delivery import INTERNATIONAL, ShipmentDocument
from newerp.domain.international import InternationalShipment, Item, Recipient, Seat, Terms

SENDER_ID = "international"  # the erp_shipment_sender row of an international waybill: one sender, from the settings


class RecipientInfo(Dto):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    country_code: str | None = None
    region: str | None = None
    city: str | None = None
    street: str | None = None
    building: str | None = None
    flat: str | None = None
    post_code: str | None = None


class TermsInfo(Dto):
    """Customs and delivery terms; empty ones take the old site's defaults (shown filled in the form)."""

    incoterm: str | None = None
    export_reason: str | None = None
    payer_fees_customs: str | None = None
    cargo_category: str | None = None
    delivery_type: str | None = None
    parcel_description: str | None = None

    def to_domain(self) -> Terms:
        return Terms.of(
            self.incoterm, self.export_reason, self.payer_fees_customs, self.cargo_category, self.delivery_type,
            self.parcel_description,
        )  # fmt: skip

    @staticmethod
    def of(terms: Terms) -> "TermsInfo":
        return TermsInfo(
            incoterm=terms.incoterm, export_reason=terms.export_reason, payer_fees_customs=terms.payer_fees_customs,
            cargo_category=terms.cargo_category, delivery_type=terms.delivery_type,
            parcel_description=terms.parcel_description,
        )  # fmt: skip


class ItemInfo(Dto):
    """A customs invoice line. weight_grams: the whole line's weight."""

    name: str | None = None
    name_eng: str | None = None
    hs_code: str | None = None
    weight_grams: int = 0
    amount: Money | None = None
    cost: Money | None = None


class HsCodeDto(Dto):
    """A customs code hint: label is the product and subcategory in Ukrainian, name_eng the English name."""

    hs_code: str
    label: str
    name_eng: str


class CountryDto(Dto):
    code: str
    name: str


class CreateInternationalShipmentRequest(Dto):
    """parcels: sizes in cm, weight in kg (the weight matters only with several seats)."""

    recipient: RecipientInfo | None = None
    terms: TermsInfo | None = None
    items: list[ItemInfo] | None = None
    parcels: list[ShipmentSeat] | None = None
    declared_cost: Decimal | None = None
    currency: str | None = None


class InternationalFormDto(Dto):
    """The recipient and lines from the order (HS codes, English names and weight are typed by hand). test: only a
    test waybill will be made."""

    recipient: RecipientInfo
    terms: TermsInfo
    items: list[ItemInfo]
    declared_cost: Money
    currency: str
    seat_presets: list[SeatPreset]
    can_create: bool
    test: bool


@dataclass(frozen=True)
class InternationalOrderLine:
    name: str
    quantity: int
    total: Decimal


@dataclass(frozen=True)
class InternationalOrderInfo:
    """Order data for the international form. country: the text from the order, as the shop wrote it."""

    first_name: str
    last_name: str
    telephone: str
    email: str
    country: str
    region: str
    city: str
    post_code: str
    address: str
    total: Decimal
    currency_code: str
    lines: list[InternationalOrderLine]


class InternationalOrders(Protocol):
    async def get_international_order_info(self, order_id: int) -> InternationalOrderInfo | None:
        """The shipping address abroad and the order lines; None if there is no such order."""
        ...


class NovaPostInternationalClient(Protocol):
    """Nova Post International (api.novapost.com). Without a key a stub is used."""

    @property
    def is_configured(self) -> bool: ...

    @property
    def can_create_documents(self) -> bool:
        """With a key, or with fake_documents in development."""
        ...

    async def search_hs_codes(self, keyword: str, country_code: str) -> list[HsCodeDto]: ...

    async def create(self, shipment: InternationalShipment) -> CreatedShipment:
        """Returns the shipment id (kept as the document Ref), number and cost."""
        ...

    async def delete(self, shipment_id: str) -> None: ...


def countries() -> list[CountryDto]:
    """All countries but Ukraine, by Ukrainian name (the shop's database has no table of countries)."""
    rows = [CountryDto(code=code, name=name) for code, name, _ in COUNTRIES if code != "UA"]
    return sorted(rows, key=lambda c: ukrainian_key(c.name))


def country_code(country: str | None) -> str:
    """The ISO code of the order's country by Ukrainian or English name; empty if unknown or Ukraine."""
    name = (country or "").strip().casefold()
    if not name:
        return ""
    for code, ukrainian, english in COUNTRIES:
        if code != "UA" and name in (code.casefold(), ukrainian.casefold(), english.casefold()):
            return code
    return ""


class InternationalShipmentService:
    def __init__(
        self,
        repository: ShipmentRepository,
        orders: InternationalOrders,
        client: NovaPostInternationalClient,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._orders = orders
        self._client = client
        self._clock = clock

    async def form(self, order_id: int) -> InternationalFormDto:
        order = await self._require_order(order_id)
        # The order keeps the address in one line: it goes into the street, the building is typed separately.
        recipient = RecipientInfo(
            first_name=order.first_name, last_name=order.last_name, phone=order.telephone, email=order.email,
            country_code=country_code(order.country), region=order.region, city=order.city, street=order.address,
            building="", flat="", post_code=order.post_code,
        )  # fmt: skip
        items = [
            ItemInfo(name=line.name, name_eng="", hs_code="", weight_grams=0, amount=line.quantity, cost=line.total)
            for line in order.lines
        ]
        can_create = self._client.can_create_documents
        return InternationalFormDto(
            recipient=recipient, terms=TermsInfo.of(Terms()), items=items, declared_cost=order.total,
            currency=order.currency_code, seat_presets=SEAT_PRESETS, can_create=can_create,
            test=can_create and not self._client.is_configured,
        )  # fmt: skip

    async def hs_codes(self, keyword: str | None, country: str | None) -> list[HsCodeDto]:
        if keyword is None or len(keyword.strip()) < 2:
            return []
        return await self._client.search_hs_codes(keyword.strip(), (country or "").strip().upper())

    async def create(self, order_id: int, request: CreateInternationalShipmentRequest, user_id: int) -> ShipmentDto:
        if not self._client.can_create_documents:
            raise DomainError("Ключ API Nova Post International не задано, тож міжнародну накладну створити не можна.")
        await self._require_order(order_id)
        if await self._repository.get_stored(order_id) is not None:
            raise DomainError("За замовленням уже є ТТН. Щоб створити нову, спершу видаліть стару.")

        r = request.recipient
        shipment = InternationalShipment.of(
            order_id,
            None if r is None else Recipient(**r.model_dump()),
            None if request.terms is None else request.terms.to_domain(),
            None
            if request.items is None
            else [Item(i.name, i.name_eng, i.hs_code, i.weight_grams, i.amount, i.cost) for i in request.items],
            None if request.parcels is None else [Seat(p.weight, p.width, p.length, p.height) for p in request.parcels],
            request.declared_cost,
            request.currency,
        )
        created = await self._client.create(shipment)
        document = ShipmentDocument(order_id, created.number, created.cost, created.ref, INTERNATIONAL)
        self._repository.add(document, SENDER_ID, user_id, self._clock.now())
        await self._repository.save_changes()
        return await self._repository.get_for_order(order_id)

    async def _require_order(self, order_id: int) -> InternationalOrderInfo:
        order = await self._orders.get_international_order_info(order_id)
        if order is None:
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")
        return order
