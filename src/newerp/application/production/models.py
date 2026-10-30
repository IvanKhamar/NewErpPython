from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from newerp.application.common import Dto, Money
from newerp.application.stock.models import PlaceDto

# ── Requests ──


class SizeRequest(Dto):
    name: str | None = None
    percent: Decimal = Decimal(0)


class SaveProductionModelRequest(Dto):
    """type: "clothing" (own sewing) or "ready_made" (ready goods from a supplier)."""

    name: str | None = None
    sizes: list[SizeRequest] | None = None
    type: str = "clothing"
    min_total: int = 50
    sort_order: int = 0


class CategoryLinkRequest(Dto):
    category_id: int = 0
    name_filter: str | None = None
    match_exact: bool = False


class SizeOptionRequest(Dto):
    size_id: int = 0
    option_value_id: int | None = None


class SetProductionLinksRequest(Dto):
    """Fully replaces the model's product links. size_options changes only the listed sizes."""

    product_ids: list[int] | None = None
    categories: list[CategoryLinkRequest] | None = None
    size_options: list[SizeOptionRequest] | None = None


class SaveComponentRequest(Dto):
    name: str | None = None
    sort_order: int = 0


class ComponentBomRowRequest(Dto):
    """size_id None: for all sizes; a row with size_id overrides it for that size."""

    size_id: int | None = None
    item_id: int = 0
    qty_per_unit: Decimal = Decimal(0)


class SetComponentBomRequest(Dto):
    rows: list[ComponentBomRowRequest] | None = None


class SewnBomRowRequest(Dto):
    """Either component_id (a cut part) or item_id (a consumable directly)."""

    size_id: int | None = None
    component_id: int | None = None
    item_id: int | None = None
    qty_per_unit: Decimal = Decimal(0)


class SetSewnBomRequest(Dto):
    rows: list[SewnBomRowRequest] | None = None


# ── Answers ──


class ProductionModelSummaryDto(Dto):
    """deficit: how many sewn pieces are missing up to min_total (0 if enough)."""

    id: int
    name: str
    type: str
    min_total: int
    total_cut: Money
    total_sewn: Money
    deficit: Money


class ProductionSizeDto(Dto):
    id: int
    name: str
    percent: Money
    cut_stock: Money
    sewn_stock: Money
    option_value_id: int | None


class CategoryLinkDto(Dto):
    category_id: int
    name_filter: str
    match_exact: bool


class ComponentBomLineDto(Dto):
    size_id: int | None
    item_id: int
    item_name: str | None
    unit: str | None
    qty_per_unit: Money


class ComponentDto(Dto):
    """stock: cut parts by size id."""

    id: int
    name: str
    sort_order: int
    stock: dict[int, Money]
    bom: list[ComponentBomLineDto]


class SewnBomLineDto(Dto):
    size_id: int | None
    component_id: int | None
    component_name: str | None
    item_id: int | None
    item_name: str | None
    unit: str | None
    qty_per_unit: Money


class ProductionModelDto(Dto):
    id: int
    name: str
    type: str
    min_total: int
    sort_order: int
    sizes: list[ProductionSizeDto]
    product_ids: list[int]
    categories: list[CategoryLinkDto]
    components: list[ComponentDto]
    sewn_bom: list[SewnBomLineDto]


# ── Cut and sewn operations ──


class ProductionOperation(StrEnum):
    """The old module's buttons "+Покроєно", "В пошив", "+Пошито", "Відвантаж."."""

    CUT = "cut"  # cut stock grows, consumables are written off by the cutting BOM, parts stock grows
    SEND_TO_SEWING = "to-sewing"  # cut sets went to the seamstresses, cut stock falls
    SEWN = "sewn"  # sewn pieces grow, parts and consumables are written off by the sewing BOM
    SHIP = "ship"  # left not through an order: sewn pieces fall


class SizeQuantity(Dto):
    size_id: int = 0
    quantity: Decimal = Decimal(0)


class ProductionOperationRequest(Dto):
    """warehouse_id only for "sewn" and "ship": where pieces came to or left from; None is the main warehouse."""

    quantities: list[SizeQuantity] | None = None
    comment: str | None = None
    warehouse_id: int | None = None


class SizeChangeDto(Dto):
    size_id: int
    size_name: str
    before: Money
    after: Money


class ProductionOperationResult(Dto):
    """warnings: what was short when writing off (what there was got written off)."""

    sizes: list[SizeChangeDto]
    warnings: list[str]


class SizeCountRequest(Dto):
    """Counted (not a difference) of one size; None: this stock was not counted."""

    size_id: int = 0
    cut: Decimal | None = None
    sewn: Decimal | None = None


class ComponentCountRequest(Dto):
    component_id: int = 0
    size_id: int = 0
    actual: Decimal = Decimal(0)


class ProductionStocktakingRequest(Dto):
    """warehouse_id: where only sewn pieces were counted; None is the main warehouse, where everything is counted."""

    sizes: list[SizeCountRequest] | None = None
    components: list[ComponentCountRequest] | None = None
    comment: str | None = None
    warehouse_id: int | None = None


class ProductionStocktakingResult(Dto):
    """cut and sewn: sizes whose stock changed; components: how many parts stocks changed."""

    cut: list[SizeChangeDto]
    sewn: list[SizeChangeDto]
    components: int


class ProductionLogDto(Dto):
    id: int
    production_id: int
    production_name: str | None
    size_id: int
    size_name: str
    stage: str
    direction: str
    quantity: Money
    stock_before: Money
    stock_after: Money
    comment: str
    user_id: int
    username: str
    created_at: datetime


@dataclass(frozen=True)
class ProductionLogFilter:
    production_id: int | None = None
    stage: str | None = None  # "cut" or "sewn"
    limit: int = 100


class StorefrontSyncResult(Dto):
    """models: how many models were pushed; products: how many store products were updated."""

    models: int
    products: int


# ── Orders and the sewing plan ──


@dataclass(frozen=True)
class OrderSizeLine:
    """An order line with what stock needs: the linked model and the OpenCart option values to find the size in."""

    product_id: int
    product_name: str
    quantity: int
    production_id: int | None
    option_value_ids: list[int]


@dataclass(frozen=True)
class PlanOrder:
    """An active order for the sewing plan: lines with model and size, and what it already holds (by size id)."""

    order_id: int
    date_added: datetime
    lines: list[OrderSizeLine]
    held: dict[int, Decimal]


class SewingPlanRowDto(Dto):
    production_id: int
    model_name: str
    size_id: int
    size_name: str
    ordered: Money
    missing: Money
    sewn_stock: Money
    cut_stock: Money
    to_sew: Money
    from_cut: Money
    to_cut: Money
    deadline: date | None
    overdue: bool
    order_ids: list[int]


class SewingPlanIssueDto(Dto):
    """A product of active orders the plan could not count (no model, or an unlinked size)."""

    product_name: str
    reason: str
    order_ids: list[int]


class SewingPlanDto(Dto):
    order_count: int
    rows: list[SewingPlanRowDto]
    issues: list[SewingPlanIssueDto]


# ── Sewn pieces by warehouse ──


class SizePlacesDto(Dto):
    """A size with sewn pieces by place: the main warehouse first, then warehouses with a non-zero quantity."""

    size_id: int
    size_name: str
    sewn: Money
    places: list[PlaceDto]


class TransferSewnRequest(Dto):
    """None in from/to: the main warehouse."""

    size_id: int = 0
    from_warehouse_id: int | None = None
    to_warehouse_id: int | None = None
    quantity: Decimal = Decimal(0)
    comment: str | None = None


class SewnTransferDto(Dto):
    id: int
    size_id: int
    size_name: str
    from_warehouse_id: int | None
    from_: str = Field(alias="from")  # "from" is a Python keyword
    to_warehouse_id: int | None
    to: str
    quantity: Money
    comment: str
    username: str
    created_at: datetime


class SewnByWarehousePlace(Dto):
    warehouse_id: int | None
    name: str


class SewnByWarehouseRow(Dto):
    production_id: int
    model_name: str
    size_id: int
    size_name: str
    total: Money
    quantities: list[Money]


class SewnByWarehouseDto(Dto):
    places: list[SewnByWarehousePlace]
    rows: list[SewnByWarehouseRow]


@dataclass(frozen=True)
class SewnBalanceRow:
    size_id: int
    warehouse_id: int
    quantity: Decimal


@dataclass(frozen=True)
class ActiveSizeRow:
    production_id: int
    model_name: str
    size_id: int
    size_name: str
    sewn: Decimal
