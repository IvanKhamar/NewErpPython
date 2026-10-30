"""Nova Poshta delivery: tracking, reminders, waybills and the branch directory (based on the old site's module)."""

from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from newerp.application.common import Clock, NotFoundError
from newerp.application.delivery.models import (
    SEAT_PRESETS,
    CityDto,
    CreateShipmentRequest,
    NewShipment,
    ReminderDto,
    ShipmentDto,
    ShipmentFormDto,
    ShipmentPrintKind,
    StoredShipment,
    TrackingRefreshResult,
    TrackingRequest,
    WarehouseDto,
    WarehouseRefreshResult,
)
from newerp.application.delivery.ports import NovaPoshtaClient, ShipmentRepository, WarehouseDirectory
from newerp.domain.common import DomainError
from newerp.domain.delivery import INTERNATIONAL, ShipmentDocument
from newerp.domain.sms import normalize_phone

TRACKING_BATCH_SIZE = 100  # the Nova Poshta API takes up to 100 waybills per tracking request
REMIND_DAYS_BEFORE = 3  # how many days before paid storage to remind (the old site: exactly 3)
SUGGESTION_LIMIT = 20  # suggestions in the city and branch autocomplete
CASH_ON_DELIVERY_PAYMENT_CODE = "cod"  # OpenCart's "pay on receipt": cash on delivery is offered for it


class DeliveryService:
    def __init__(self, repository: ShipmentRepository, client: NovaPoshtaClient, clock: Clock) -> None:
        self._repository = repository
        self._client = client
        self._clock = clock

    async def refresh_tracking(self) -> TrackingRefreshResult:
        """Updates the statuses of all open waybills; the changed ones are saved together."""
        if not self._client.is_configured:
            return TrackingRefreshResult(client_configured=False, checked=0, updated=0)
        open_shipments = await self._repository.list_open_for_tracking()
        by_number: dict[str, ShipmentDocument] = {}
        for shipment in open_shipments:
            by_number.setdefault(shipment.document.number, shipment.document)

        updated = 0
        for start in range(0, len(open_shipments), TRACKING_BATCH_SIZE):
            batch = open_shipments[start : start + TRACKING_BATCH_SIZE]
            infos = await self._client.track([TrackingRequest(s.document.number, s.phone) for s in batch])
            for info in infos:
                document = by_number.get(info.number)
                if document is not None and document.apply_tracking(info):
                    updated += 1
        await self._repository.save_changes()
        return TrackingRefreshResult(client_configured=True, checked=len(open_shipments), updated=updated)

    async def reminders(self) -> list[ReminderDto]:
        """
        Whom to remind to pick up the parcel: paid storage starts from today up to three days ahead. The old site
        showed only those exactly 3 days ahead, so a missed day dropped the person from the list.
        """
        today = self._clock.now().date()
        return await self._repository.list_reminders(today, today + timedelta(days=REMIND_DAYS_BEFORE))

    async def mark_reminded(self, order_id: int) -> None:
        if not await self._repository.mark_reminded(order_id, self._clock.now()):
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")


class InternationalDeleter(Protocol):
    """Deletes Nova Post International waybills (set when that module is wired)."""

    async def delete(self, document_ref: str) -> None: ...


class ShipmentService:
    """Nova Poshta waybills: create, delete, print. Branch or parcel locker delivery only, as in .NET."""

    def __init__(
        self,
        repository: ShipmentRepository,
        directory: WarehouseDirectory,
        client: NovaPoshtaClient,
        clock: Clock,
        international: InternationalDeleter | None = None,
    ) -> None:
        self._repository = repository
        self._directory = directory
        self._client = client
        self._clock = clock
        self._international = international

    async def form(self, order_id: int) -> ShipmentFormDto:
        order = await self._repository.get_order_shipping_info(order_id)
        if order is None:
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")
        # City and branch are written in the order as text; if the directory has them, they are filled in at once.
        city = await self._directory.find_city(order.city.strip()) if order.city.strip() else None
        warehouse = (
            await self._directory.find_warehouse(city.ref, order.warehouse.strip())
            if city is not None and order.warehouse.strip()
            else None
        )
        # Cash on delivery, as on the old site, is the total minus the advance.
        cod = (
            max(order.total - order.prepayment, Decimal(0))
            if order.payment_code == CASH_ON_DELIVERY_PAYMENT_CODE
            else Decimal(0)
        )
        can_create = self._client.can_create_documents
        return ShipmentFormDto(
            first_name=order.first_name, last_name=order.last_name, phone=order.telephone, city_name=order.city,
            city=city, warehouse_name=order.warehouse, warehouse=warehouse, declared_cost=order.total,
            cash_on_delivery=cod, senders=self._client.senders, dropoff_points=self._client.dropoff_points,
            seat_presets=SEAT_PRESETS, can_create=can_create, test_mode=can_create and not self._client.is_configured,
        )  # fmt: skip

    async def create(self, order_id: int, request: CreateShipmentRequest, user_id: int) -> ShipmentDto:
        if not self._client.can_create_documents:
            raise DomainError("Ключ API Нової Пошти не задано, тож ТТН створити не можна.")
        if await self._repository.get_order_shipping_info(order_id) is None:
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")
        if await self._repository.get_stored(order_id) is not None:
            raise DomainError("За замовленням уже є ТТН. Щоб створити нову, спершу видаліть стару.")

        shipment = await self._validate(order_id, request)
        created = await self._client.create(shipment)
        document = ShipmentDocument(order_id, created.number, created.cost, created.ref)
        self._repository.add(document, shipment.sender_id, user_id, self._clock.now())
        await self._repository.save_changes()
        return await self._repository.get_for_order(order_id)

    async def delete(self, order_id: int) -> None:
        """Deletes the waybill at Nova Poshta first, then here. If Nova Poshta refused, the record stays."""
        stored = await self._require_stored(order_id)
        if stored.document.is_final:
            raise DomainError("Посилку вже отримано чи повернено, таку ТТН видалити не можна.")
        if stored.document.carrier == INTERNATIONAL:
            if self._international is None:
                raise RuntimeError("Клієнт Nova Post International не підключено.")
            await self._international.delete(stored.document.ref)
        else:
            await self._client.delete(self._sender_of(stored), stored.document.ref)
        await self._repository.delete(stored.document.id)

    async def print(self, order_id: int, kind: ShipmentPrintKind) -> tuple[bytes, str]:
        stored = await self._require_stored(order_id)
        pdf = await self._client.print(self._sender_of(stored), stored.document.ref, kind)
        prefix = "ttn" if kind == ShipmentPrintKind.DOCUMENT else "marking"
        return pdf, f"{prefix}-{stored.document.number}.pdf"

    async def search_cities(self, search: str | None) -> list[CityDto]:
        if not search or not search.strip():
            return []
        return await self._directory.search_cities(search.strip(), SUGGESTION_LIMIT)

    async def search_warehouses(self, city_ref: str, search: str | None) -> list[WarehouseDto]:
        return await self._directory.search_warehouses(city_ref, (search or "").strip(), SUGGESTION_LIMIT)

    async def refresh_warehouses(self) -> WarehouseRefreshResult:
        """Loads a fresh branch directory from the API and replaces the local one (and the old storefront's)."""
        if not self._client.is_configured:
            return WarehouseRefreshResult(client_configured=False, count=0)
        warehouses = await self._client.fetch_warehouses()
        # An empty answer is almost surely a failure, not "all branches closed": the directory stays.
        if not warehouses:
            raise DomainError("Нова Пошта повернула порожній довідник відділень, локальний довідник не змінено.")
        await self._directory.replace_all(warehouses)
        return WarehouseRefreshResult(client_configured=True, count=len(warehouses))

    async def _validate(self, order_id: int, request: CreateShipmentRequest) -> NewShipment:
        first_name = _name(request.first_name, "Ім'я")
        last_name = _name(request.last_name, "Прізвище")
        phone = normalize_phone(request.phone)
        if not (request.city_ref or "").strip() or not (request.warehouse_ref or "").strip():
            raise DomainError("Оберіть місто й відділення з довідника.")
        if not await self._directory.warehouse_exists(request.city_ref, request.warehouse_ref):
            raise DomainError("Такого відділення в цьому місті немає в довіднику.")
        if request.declared_cost <= 0:
            raise DomainError("Оголошена вартість має бути більшою за нуль.")
        if request.cash_on_delivery < 0:
            raise DomainError("Накладений платіж не може бути від'ємним.")
        seats = request.seats or []
        if not seats:
            raise DomainError("Додайте хоча б одне місце (коробку).")
        if any(s.weight <= 0 or s.width < 0 or s.length < 0 or s.height < 0 for s in seats):
            raise DomainError("Вага місця має бути більшою за нуль, а розміри — не від'ємними.")

        sender = _pick(self._client.senders, request.sender_id, "відправника")
        point = _pick(self._client.dropoff_points, request.dropoff_point_id, "місце відправки")
        return NewShipment(
            sender_id=sender.id, dropoff_point_id=point.id, order_id=order_id, ship_date=self._clock.now().date(),
            first_name=first_name, last_name=last_name, middle_name=(request.middle_name or "").strip(), phone=phone,
            city_ref=request.city_ref, warehouse_ref=request.warehouse_ref, declared_cost=request.declared_cost,
            cash_on_delivery=request.cash_on_delivery, sender_pays=request.sender_pays, seats=seats,
        )  # fmt: skip

    async def _require_stored(self, order_id: int) -> StoredShipment:
        stored = await self._repository.get_stored(order_id)
        if stored is None:
            raise NotFoundError(f"У замовлення {order_id} немає ТТН.")
        return stored

    def _sender_of(self, stored: StoredShipment) -> str:
        """Waybills made on the old site have no sender record: the default sender is used for them."""
        return stored.sender_id or _pick(self._client.senders, None, "відправника").id


def _name(value: str | None, field: str) -> str:
    """As the old site: Nova Poshta does not take the "`" apostrophe, it becomes "'"."""
    if not value or not value.strip():
        raise DomainError(f"Поле «{field}» обов'язкове.")
    return value.strip().replace("`", "'")


def _pick[T](options: Sequence[T], option_id: str | None, what: str) -> T:
    if option_id is not None:
        found = next((o for o in options if o.id == option_id), None)
        if found is None:
            raise DomainError(f"Невідомо {what}: {option_id}.")
        return found
    found = next((o for o in options if o.is_default), None) or next(iter(options), None)
    if found is None:
        raise DomainError(f"У налаштуваннях Нової Пошти не задано {what}.")
    return found
