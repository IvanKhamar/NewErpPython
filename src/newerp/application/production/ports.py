"""Production repositories; they share the request's session, so one save_changes saves everything added."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from newerp.application.production.models import (
    ActiveSizeRow,
    OrderSizeLine,
    PlanOrder,
    ProductionLogDto,
    ProductionLogFilter,
    ProductionModelDto,
    ProductionModelSummaryDto,
    SewnBalanceRow,
    StorefrontSyncResult,
)
from newerp.domain.production import (
    ComponentStock,
    ProductionComponent,
    ProductionLogEntry,
    ProductionModel,
    SewnBalance,
    SewnTransfer,
)
from newerp.domain.stock import StockItem, StockMovement


@dataclass(frozen=True)
class ProductionModelFilter:
    type: str | None = None
    below_minimum_only: bool = False


class ProductionRepository(Protocol):
    async def list_models(self, model_filter: ProductionModelFilter) -> list[ProductionModelSummaryDto]:
        """Active models with cut and sewn totals."""
        ...

    async def get_dto(self, production_id: int) -> ProductionModelDto | None: ...

    async def find_model(self, production_id: int) -> ProductionModel | None:
        """The model with sizes, links and sewing BOM, for a change."""
        ...

    async def find_component(self, component_id: int) -> ProductionComponent | None: ...

    async def component_ids(self, production_id: int) -> list[int]: ...

    async def missing_stock_item_ids(self, item_ids: list[int]) -> list[int]:
        """Those of item_ids that are not active consumables."""
        ...

    async def sizes_in_use(self, size_ids: list[int]) -> bool:
        """Whether cutting BOMs or a non-zero parts stock refer to these sizes."""
        ...

    async def find_component_stock(self, component_id: int) -> list[ComponentStock]: ...

    async def find_components(self, production_id: int) -> list[ProductionComponent]: ...

    async def find_component_stocks(self, component_ids: list[int]) -> list[ComponentStock]: ...

    async def find_stock_items(self, item_ids: list[int]) -> list[StockItem]: ...

    async def list_log(self, log_filter: ProductionLogFilter) -> list[ProductionLogDto]: ...

    async def get_order_size_lines(self, order_id: int) -> list[OrderSizeLine]: ...

    async def find_models(self, production_ids: list[int]) -> list[ProductionModel]: ...

    async def get_sewn_held_by_order(self, order_id: int) -> dict[int, Decimal]:
        """
        How many sewn pieces of each size the order holds now: taken with "Замовлення #id" minus returned with
        "Скасування #id". Counted by the shared log, so actions of the old site count too.
        """
        ...

    def add(
        self, entity: ProductionLogEntry | StockMovement | ComponentStock | ProductionModel | ProductionComponent
    ) -> None: ...

    async def remove(self, entity: ProductionComponent | ComponentStock) -> None: ...

    async def save_changes(self) -> None: ...


class SewingPlanSource(Protocol):
    async def get_orders(self, status_ids: list[int]) -> list[PlanOrder]: ...

    async def get_production_days(self, product_ids: list[int]) -> dict[int, int]:
        """Production days by product: production_period of its first category (the smallest id)."""
        ...

    async def get_models(self, production_ids: list[int]) -> list[ProductionModel]: ...


class StorefrontStock(Protocol):
    """
    Pushes sewn stock to the store (OpenCart), like pushStockToOc of the old module: each linked size of a product
    gets the model size's sewn stock, and the product gets their sum.
    """

    async def push(self, production_ids: list[int] | None) -> StorefrontSyncResult:
        """production_ids None: all active models."""
        ...


class SewnLocationRepository(Protocol):
    async def find_balances(self, production_id: int) -> list[SewnBalance]: ...

    async def list_balances(self) -> list[SewnBalanceRow]: ...

    async def list_active_sizes(self) -> list[ActiveSizeRow]: ...

    async def list_transfers(self, production_id: int) -> list[SewnTransfer]: ...

    async def holds_stock(self, warehouse_id: int) -> bool: ...

    def add(self, entity: SewnBalance | SewnTransfer) -> None: ...
