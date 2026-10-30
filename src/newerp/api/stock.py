from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from newerp.api.deps import ClockDep, Session
from newerp.api.errors import problem
from newerp.api.files import read_upload, xlsx_file
from newerp.api.security import CurrentPrincipal, Policies, require_roles
from newerp.application.common import CreatedId, LookupItem
from newerp.application.stock.models import (
    AdjustStockRequest,
    ApplyStocktakingRequest,
    CreatePurchaseOrderRequest,
    CreateStockCategoryRequest,
    CreateStockSupplierRequest,
    ItemPlacementDto,
    PurchaseOrderDto,
    PurchaseOrderSummaryDto,
    ReceivePurchaseOrderRequest,
    ReorderReportDto,
    SaveStockItemRequest,
    SetPurchaseOrderLinesRequest,
    StockByWarehouseDto,
    StockItemDto,
    StockMovementDto,
    StockSupplierDto,
    StocktakingPreviewDto,
    StocktakingResult,
    StockTransferDto,
    TransferStockRequest,
)
from newerp.application.stock.ports import StockItemFilter
from newerp.application.stock.services import (
    MAX_FILE_BYTES,
    PurchaseOrderService,
    ReorderService,
    StockLocationService,
    StockService,
    StocktakingService,
)
from newerp.config import get_settings
from newerp.infrastructure.locations import SqlLocationStore
from newerp.infrastructure.spreadsheets import OpenpyxlSpreadsheet
from newerp.infrastructure.stock.repositories import (
    SqlPurchaseOrderRepository,
    SqlStockLocationRepository,
    SqlStockRepository,
)

router = APIRouter(prefix="/stock", tags=["Склад: розхідники"], dependencies=[Depends(require_roles(*Policies.STOCK))])


# ── Wiring: all repositories share the request's session, so one commit saves everything ──


def get_stock_repository(session: Session) -> SqlStockRepository:
    return SqlStockRepository(session)


def get_purchase_orders(session: Session) -> SqlPurchaseOrderRepository:
    return SqlPurchaseOrderRepository(session)


def get_stock_locations(session: Session) -> SqlStockLocationRepository:
    return SqlStockLocationRepository(session)


Repository = Annotated[SqlStockRepository, Depends(get_stock_repository)]
PurchaseOrders = Annotated[SqlPurchaseOrderRepository, Depends(get_purchase_orders)]
Locations = Annotated[SqlStockLocationRepository, Depends(get_stock_locations)]


def get_reorder_service(repository: Repository, orders: PurchaseOrders) -> ReorderService:
    return ReorderService(repository, orders, get_settings().stock.reorder_target_factor)


Reorder = Annotated[ReorderService, Depends(get_reorder_service)]


def get_stock_service(repository: Repository, locations: Locations, clock: ClockDep) -> StockService:
    return StockService(repository, locations, clock)


def get_purchase_order_service(
    session: Session, orders: PurchaseOrders, repository: Repository, reorder: Reorder, locations: Locations,
    clock: ClockDep,
) -> PurchaseOrderService:  # fmt: skip
    return PurchaseOrderService(orders, repository, reorder, locations, SqlLocationStore(session), clock)


def get_location_service(
    session: Session, repository: Repository, locations: Locations, clock: ClockDep
) -> StockLocationService:
    return StockLocationService(repository, locations, SqlLocationStore(session), clock)


def get_stocktaking_service(
    session: Session, repository: Repository, locations: Locations, clock: ClockDep
) -> StocktakingService:
    return StocktakingService(repository, locations, SqlLocationStore(session), OpenpyxlSpreadsheet(), clock)


Stock = Annotated[StockService, Depends(get_stock_service)]
Orders = Annotated[PurchaseOrderService, Depends(get_purchase_order_service)]
ByWarehouse = Annotated[StockLocationService, Depends(get_location_service)]
Stocktaking = Annotated[StocktakingService, Depends(get_stocktaking_service)]


def created(response: Response, path: str, new_id: int) -> CreatedId:
    response.headers["Location"] = f"{path}/{new_id}"
    return CreatedId(id=new_id)


# ── Categories, suppliers, items ──


@router.get("/categories")
async def list_categories(repository: Repository) -> list[LookupItem]:
    return await repository.list_categories()


@router.post("/categories", status_code=201)
async def create_category(request: CreateStockCategoryRequest, service: Stock, response: Response) -> CreatedId:
    return created(response, "/stock/categories", await service.create_category(request))


@router.get("/suppliers")
async def list_suppliers(repository: Repository) -> list[StockSupplierDto]:
    return await repository.list_suppliers()


@router.post("/suppliers", status_code=201)
async def create_supplier(request: CreateStockSupplierRequest, service: Stock, response: Response) -> CreatedId:
    return created(response, "/stock/suppliers", await service.create_supplier(request))


@router.get("/items", summary="Позиції складу; belowMinimumOnly=true показує те, що треба дозамовити")
async def list_items(
    repository: Repository,
    category_id: Annotated[int | None, Query(alias="categoryId")] = None,
    below_minimum_only: Annotated[bool, Query(alias="belowMinimumOnly")] = False,
) -> list[StockItemDto]:
    return await repository.list_items(StockItemFilter(category_id, below_minimum_only))


@router.get("/reorder", summary="Що дозамовити: позиції нижче мінімуму за постачальниками, з текстом замовлення")
async def reorder(service: Reorder) -> ReorderReportDto:
    return await service.get_report()


@router.get("/items/{item_id}", response_model=StockItemDto)
async def get_item(item_id: int, repository: Repository) -> StockItemDto | Response:
    item = await repository.get_item_dto(item_id)
    return item if item is not None else problem(404, "Not Found")


@router.post("/items", status_code=201)
async def create_item(request: SaveStockItemRequest, service: Stock, response: Response) -> CreatedId:
    return created(response, "/stock/items", await service.create_item(request))


@router.put("/items/{item_id}", status_code=204, summary="Змінити позицію (залишок — лише через adjustments)")
async def update_item(item_id: int, request: SaveStockItemRequest, service: Stock) -> None:
    await service.update_item(item_id, request)


@router.post("/items/{item_id}/adjustments", summary="Прихід (+) або витрата (-) з записом у журнал руху")
async def adjust(
    item_id: int, request: AdjustStockRequest, principal: CurrentPrincipal, service: Stock
) -> StockMovementDto:
    return await service.adjust(item_id, request, principal.user_id, principal.username)


@router.put("/items/{item_id}/min-quantity", status_code=204)
async def set_min_quantity(
    item_id: int, min_quantity: Annotated[Decimal, Query(alias="minQuantity")], service: Stock
) -> None:
    await service.set_min_quantity(item_id, min_quantity)


@router.get("/items/{item_id}/movements", summary="Журнал руху позиції")
async def list_movements(item_id: int, repository: Repository) -> list[StockMovementDto]:
    return await repository.list_movements(item_id)


# ── Purchase orders ──

orders = APIRouter(prefix="/purchase-orders", tags=["Склад: замовлення постачальникам"])


@orders.get("", summary="Замовлення постачальникам; status = draft, ordered, received або cancelled")
async def list_purchase_orders(repository: PurchaseOrders, status: str | None = None) -> list[PurchaseOrderSummaryDto]:
    return await repository.list_orders(status)


@orders.get("/{order_id}", response_model=PurchaseOrderDto, summary="Замовлення з рядками і текстом для постачальника")
async def get_purchase_order(order_id: int, repository: PurchaseOrders) -> PurchaseOrderDto | Response:
    order = await repository.get_dto(order_id)
    return order if order is not None else problem(404, "Not Found")


@orders.post("", status_code=201, summary="Створити чернетку вручну")
async def create_purchase_order(
    request: CreatePurchaseOrderRequest, principal: CurrentPrincipal, service: Orders, response: Response
) -> CreatedId:
    return created(response, "/stock/purchase-orders", await service.create(request, principal.user_id))


@orders.post("/from-reorder/{supplier_id}", status_code=201, summary="Чернетка зі звіту «що дозамовити»")
async def create_from_reorder(
    supplier_id: int, principal: CurrentPrincipal, service: Orders, response: Response
) -> CreatedId:
    return created(
        response, "/stock/purchase-orders", await service.create_from_reorder(supplier_id, principal.user_id)
    )


@orders.put("/{order_id}/lines", status_code=204, summary="Змінити рядки чернетки")
async def set_lines(order_id: int, request: SetPurchaseOrderLinesRequest, service: Orders) -> None:
    await service.set_lines(order_id, request)


@orders.post("/{order_id}/ordered", status_code=204, summary="Позначити, що замовлення надіслано постачальнику")
async def mark_ordered(order_id: int, service: Orders) -> None:
    await service.mark_ordered(order_id)


@orders.post("/{order_id}/receive", status_code=204, summary="Прийняти товар (можна частинами)")
async def receive(
    order_id: int, request: ReceivePurchaseOrderRequest, principal: CurrentPrincipal, service: Orders
) -> None:
    await service.receive(order_id, request, principal.user_id, principal.username)


@orders.post("/{order_id}/cancel", status_code=204)
async def cancel(order_id: int, service: Orders) -> None:
    await service.cancel(order_id)


router.include_router(orders)


# ── By warehouse ──


@router.get("/warehouses", tags=["Склад: за складами"], summary="Склади для вибору місця, без основного")
async def warehouses(service: ByWarehouse) -> list[LookupItem]:
    return await service.warehouses()


@router.get("/by-warehouse", tags=["Склад: за складами"], summary="Залишки за місцями з вартістю кожного")
async def by_warehouse(service: ByWarehouse) -> StockByWarehouseDto:
    return await service.by_warehouse()


@router.get("/items/{item_id}/places", tags=["Склад: за складами"], summary="Де лежить позиція")
async def places(item_id: int, service: ByWarehouse) -> ItemPlacementDto:
    return await service.get(item_id)


@router.post("/items/{item_id}/transfers", tags=["Склад: за складами"], summary="Перемістити між складами")
async def transfer(
    item_id: int, request: TransferStockRequest, principal: CurrentPrincipal, service: ByWarehouse
) -> ItemPlacementDto:
    return await service.transfer(item_id, request, principal.user_id, principal.username)


@router.get("/items/{item_id}/transfers", tags=["Склад: за складами"], summary="Журнал переміщень позиції")
async def transfers(item_id: int, service: ByWarehouse) -> list[StockTransferDto]:
    return await service.transfers(item_id)


@router.post(
    "/items/{item_id}/warehouses/{warehouse_id}/adjustments",
    tags=["Склад: за складами"],
    summary="Прихід (+) або витрата (-) на конкретному складі",
)
async def adjust_at_warehouse(
    item_id: int, warehouse_id: int, request: AdjustStockRequest, principal: CurrentPrincipal, service: ByWarehouse
) -> StockMovementDto:
    return await service.adjust(item_id, warehouse_id, request, principal.user_id, principal.username)


# ── Stocktaking ──

stocktaking = APIRouter(prefix="/stocktaking", tags=["Склад: інвентаризація"])


@stocktaking.get("/sheet.xlsx", summary="Аркуш для підрахунку з порожнім стовпчиком «Фактично»")
async def stocktaking_sheet(
    service: Stocktaking,
    category_id: Annotated[int | None, Query(alias="categoryId")] = None,
    warehouse_id: Annotated[int | None, Query(alias="warehouseId")] = None,
) -> Response:
    return xlsx_file(await service.sheet(category_id, warehouse_id), "inventaryzatsiia")


@stocktaking.post("/preview", summary="Що змінить заповнений аркуш (сире тіло запиту); нічого не записує")
async def stocktaking_preview(
    request: Request, service: Stocktaking, warehouse_id: Annotated[int | None, Query(alias="warehouseId")] = None
) -> StocktakingPreviewDto:
    return await service.preview(await read_upload(request, MAX_FILE_BYTES), warehouse_id)


@stocktaking.post("", summary="Застосувати пораховане; різниці — у журнал руху з коментарем «Інвентаризація»")
async def apply_stocktaking(
    request: ApplyStocktakingRequest, principal: CurrentPrincipal, service: Stocktaking
) -> StocktakingResult:
    return await service.apply(request, principal.user_id, principal.username)


router.include_router(stocktaking)
