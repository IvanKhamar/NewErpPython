"""Nova Poshta delivery: an order's waybill, tracking, reminders. Access as to orders (Admin, Manager)."""

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from newerp.api.deps import ClockDep, Session
from newerp.api.security import CurrentPrincipal, Policies, require_roles
from newerp.application.delivery.international import (
    CountryDto,
    CreateInternationalShipmentRequest,
    HsCodeDto,
    InternationalFormDto,
    InternationalShipmentService,
    countries,
)
from newerp.application.delivery.models import (
    CityDto,
    CreateShipmentRequest,
    ReminderDto,
    ShipmentDto,
    ShipmentFormDto,
    ShipmentPrintKind,
    TrackingRefreshResult,
    WarehouseDto,
    WarehouseRefreshResult,
)
from newerp.application.delivery.services import DeliveryService, ShipmentService
from newerp.config import get_settings
from newerp.infrastructure.delivery.nova_poshta import nova_poshta_client
from newerp.infrastructure.delivery.nova_post_international import nova_post_international_client
from newerp.infrastructure.delivery.repositories import SqlShipmentRepository, SqlWarehouseDirectory

orders_access = [Depends(require_roles(*Policies.ORDERS))]
shipment_router = APIRouter(prefix="/orders/{order_id}/shipment", tags=["Доставка"], dependencies=orders_access)
delivery_router = APIRouter(prefix="/delivery", tags=["Доставка"], dependencies=orders_access)


@lru_cache
def get_nova_poshta():
    """One client for the process, like the singleton in .NET DI."""
    return nova_poshta_client(get_settings().nova_poshta)


def get_shipment_service(session: Session, clock: ClockDep) -> ShipmentService:
    return ShipmentService(
        SqlShipmentRepository(session), SqlWarehouseDirectory(session), get_nova_poshta(), clock,
        get_nova_post_international(),
    )  # fmt: skip


def get_delivery_service(session: Session, clock: ClockDep) -> DeliveryService:
    return DeliveryService(SqlShipmentRepository(session), get_nova_poshta(), clock)


Shipments = Annotated[ShipmentService, Depends(get_shipment_service)]
Delivery = Annotated[DeliveryService, Depends(get_delivery_service)]


@shipment_router.get("", response_model=ShipmentDto, summary="ТТН замовлення зі статусом (204 — ТТН ще немає)")
async def get_shipment(order_id: int, session: Session) -> ShipmentDto | Response:
    shipment = await SqlShipmentRepository(session).get_for_order(order_id)
    return shipment if shipment is not None else Response(status_code=204)


@shipment_router.get("/form", summary="Форма нової ТТН, заповнена даними замовлення")
async def shipment_form(order_id: int, service: Shipments) -> ShipmentFormDto:
    return await service.form(order_id)


@shipment_router.post("", summary="Створити ТТН (без ключа API — лише тестову, якщо novaPoshta.fakeDocuments)")
async def create_shipment(
    order_id: int, request: CreateShipmentRequest, principal: CurrentPrincipal, service: Shipments
) -> ShipmentDto:
    return await service.create(order_id, request, principal.user_id)


@shipment_router.delete("", status_code=204, summary="Видалити ТТН у Новій Пошті й у нас")
async def delete_shipment(order_id: int, service: Shipments) -> None:
    await service.delete(order_id)


@shipment_router.get("/print", summary="PDF накладної (kind=Document) чи маркування (kind=Markings)")
async def print_shipment(
    order_id: int, service: Shipments, kind: ShipmentPrintKind = ShipmentPrintKind.DOCUMENT
) -> Response:
    pdf, _ = await service.print(order_id, kind)
    # No file name, so the browser opens the PDF to print instead of downloading it.
    return Response(pdf, media_type="application/pdf")


@delivery_router.post(
    "/tracking/refresh", summary="Оновити статуси незакритих ТТН (без ключа: clientConfigured = false)"
)
async def refresh_tracking(service: Delivery) -> TrackingRefreshResult:
    return await service.refresh_tracking()


@delivery_router.get("/reminders", summary="Посилки, у яких за 0–3 дні почнеться платне зберігання")
async def reminders(service: Delivery) -> list[ReminderDto]:
    return await service.reminders()


@delivery_router.post("/reminders/{order_id}/done", status_code=204, summary="Позначити, що клієнту нагадали")
async def reminded(order_id: int, service: Delivery) -> None:
    await service.mark_reminded(order_id)


@delivery_router.get("/cities", summary="Міста з довідника, назва яких починається з search")
async def cities(service: Shipments, search: str | None = None) -> list[CityDto]:
    return await service.search_cities(search)


@delivery_router.get("/warehouses", summary="Робочі відділення й поштомати міста, у назві яких є search")
async def warehouses(
    service: Shipments, city_ref: Annotated[str, Query(alias="cityRef")], search: str | None = None
) -> list[WarehouseDto]:
    return await service.search_warehouses(city_ref, search)


@delivery_router.post("/warehouses/refresh", summary="Завантажити свіжий довідник відділень з Нової Пошти")
async def refresh_warehouses(service: Shipments) -> WarehouseRefreshResult:
    return await service.refresh_warehouses()


# ── Waybills abroad (Nova Post International): one waybill per order whatever the carrier ──


@lru_cache
def get_nova_post_international():
    return nova_post_international_client(get_settings().nova_post_international)


def get_international_service(session: Session, clock: ClockDep) -> InternationalShipmentService:
    repository = SqlShipmentRepository(session)
    return InternationalShipmentService(repository, repository, get_nova_post_international(), clock)


International = Annotated[InternationalShipmentService, Depends(get_international_service)]


@shipment_router.get("/international/form", summary="Форма міжнародної накладної з даними замовлення")
async def international_form(order_id: int, service: International) -> InternationalFormDto:
    return await service.form(order_id)


@shipment_router.post("/international", summary="Створити накладну в Nova Post International (без ключа — тестову)")
async def create_international(
    order_id: int, request: CreateInternationalShipmentRequest, principal: CurrentPrincipal, service: International
) -> ShipmentDto:
    return await service.create(order_id, request, principal.user_id)


@delivery_router.get("/international/countries", summary="Країни отримувача (усі, крім України)")
async def international_countries() -> list[CountryDto]:
    return countries()


@delivery_router.get("/international/hs-codes", summary="Підказки кодів УКТЗЕД за словом (від двох літер)")
async def hs_codes(service: International, search: str | None = None, country: str | None = None) -> list[HsCodeDto]:
    return await service.hs_codes(search, country)
