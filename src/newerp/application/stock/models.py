from datetime import datetime
from decimal import Decimal

from pydantic import Field

from newerp.application.common import Dto, Money


class StockItemDto(Dto):
    id: int
    name: str
    unit: str
    category_id: int | None
    category_name: str | None
    supplier_id: int | None
    supplier_name: str | None
    quantity: Money
    min_quantity: Money
    is_below_minimum: bool
    used_for: str
    price: Money


class StockMovementDto(Dto):
    """delta: minus is use, plus is receipt. reason is the log comment."""

    id: int
    item_id: int
    delta: Money
    quantity_before: Money
    quantity_after: Money
    reason: str
    user_id: int
    username: str
    created_at: datetime


class StockSupplierDto(Dto):
    id: int
    name: str
    phone: str
    email: str
    categories: str
    notes: str


class CreateStockCategoryRequest(Dto):
    name: str | None = None
    sort_order: int = 0


class CreateStockSupplierRequest(Dto):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    categories: str | None = None
    notes: str | None = None


class SaveStockItemRequest(Dto):
    category_id: int | None = None
    supplier_id: int | None = None
    name: str | None = None
    unit: str | None = None
    min_quantity: Decimal = Decimal(0)
    used_for: str | None = None
    price: Decimal = Decimal(0)


class AdjustStockRequest(Dto):
    """delta: plus is receipt, minus is use."""

    delta: Decimal = Decimal(0)
    reason: str | None = None


# ── What to reorder ──


class ReorderLineDto(Dto):
    """on_order: already ordered and not yet received. suggested is rounded up: suppliers sell whole units."""

    item_id: int
    name: str
    unit: str
    quantity: Money
    min_quantity: Money
    on_order: Money
    suggested: Money
    price: Money
    sum: Money


class SupplierReorderDto(Dto):
    """supplier_id None: items without a supplier. message_text: a ready order text for a messenger or e-mail."""

    supplier_id: int | None
    supplier_name: str
    phone: str
    email: str
    lines: list[ReorderLineDto]
    total: Money
    message_text: str


class ReorderReportDto(Dto):
    suppliers: list[SupplierReorderDto]
    total: Money


# ── Purchase orders ──


class PurchaseOrderLineRequest(Dto):
    item_id: int = 0
    quantity: Decimal = Decimal(0)
    price: Decimal = Decimal(0)


class CreatePurchaseOrderRequest(Dto):
    supplier_id: int = 0
    lines: list[PurchaseOrderLineRequest] | None = None
    comment: str | None = None


class SetPurchaseOrderLinesRequest(Dto):
    lines: list[PurchaseOrderLineRequest] | None = None


class ReceiptLineRequest(Dto):
    line_id: int = 0
    quantity: Decimal = Decimal(0)


class ReceivePurchaseOrderRequest(Dto):
    """warehouse_id: where the delivery came; None is the main warehouse."""

    lines: list[ReceiptLineRequest] | None = None
    warehouse_id: int | None = None


class PurchaseOrderLineDto(Dto):
    id: int
    item_id: int
    item_name: str | None
    unit: str | None
    quantity: Money
    price: Money
    sum: Money
    received: Money
    remaining: Money


class PurchaseOrderDto(Dto):
    id: int
    supplier_id: int
    supplier_name: str | None
    supplier_phone: str | None
    status: str
    comment: str
    created_at: datetime
    ordered_at: datetime | None
    closed_at: datetime | None
    lines: list[PurchaseOrderLineDto]
    total: Money
    message_text: str


class PurchaseOrderSummaryDto(Dto):
    id: int
    supplier_id: int
    supplier_name: str | None
    status: str
    created_at: datetime
    line_count: int
    total: Money


# ── By warehouse ──


class StockBalanceRow(Dto):
    item_id: int
    warehouse_id: int
    quantity: Money


class PlaceDto(Dto):
    """warehouse_id None: the main warehouse."""

    warehouse_id: int | None
    name: str
    quantity: Money


class ItemPlacementDto(Dto):
    """The main warehouse always first, then warehouses with a non-zero quantity."""

    item_id: int
    name: str
    unit: str
    total: Money
    places: list[PlaceDto]


class TransferStockRequest(Dto):
    """None in from/to: the main warehouse."""

    from_warehouse_id: int | None = None
    to_warehouse_id: int | None = None
    quantity: Decimal = Decimal(0)
    comment: str | None = None


class StockTransferDto(Dto):
    id: int
    from_warehouse_id: int | None
    from_: str = Field(alias="from")  # "from" is a Python keyword
    to_warehouse_id: int | None
    to: str
    quantity: Money
    comment: str
    username: str
    created_at: datetime


class StockByWarehousePlace(Dto):
    """A report column: a place and the value of what lies there (at the item's price)."""

    warehouse_id: int | None
    name: str
    value: Money


class StockByWarehouseRow(Dto):
    """quantities: in the order of the places columns."""

    item_id: int
    name: str
    unit: str
    category_name: str | None
    total: Money
    quantities: list[Money]


class StockByWarehouseDto(Dto):
    places: list[StockByWarehousePlace]
    rows: list[StockByWarehouseRow]


# ── Stocktaking ──


class StocktakingLineDto(Dto):
    """booked: by the books now; difference = actual − booked (minus is a shortage)."""

    item_id: int
    name: str
    unit: str
    booked: Money
    actual: Money
    difference: Money


class StocktakingPreviewDto(Dto):
    """errors: rows that could not be read (nothing was written)."""

    lines: list[StocktakingLineDto]
    errors: list[str]


class StocktakingCountRequest(Dto):
    item_id: int = 0
    actual: Decimal = Decimal(0)


class ApplyStocktakingRequest(Dto):
    """Counted quantities (not differences). warehouse_id: the counted warehouse; None is the main one."""

    counts: list[StocktakingCountRequest] | None = None
    comment: str | None = None
    warehouse_id: int | None = None


class StocktakingResult(Dto):
    """changed: how many items changed; value: sum of differences × price (minus is a shortage)."""

    counted: int
    changed: int
    value: Money
