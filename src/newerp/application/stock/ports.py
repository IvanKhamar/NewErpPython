"""Stock repositories. They share one database session, so save_changes of any of them saves everything added
(like one DbContext in .NET)."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from newerp.application.common import LookupItem
from newerp.application.stock.models import (
    PurchaseOrderDto,
    PurchaseOrderSummaryDto,
    StockBalanceRow,
    StockItemDto,
    StockMovementDto,
    StockSupplierDto,
)
from newerp.domain.stock import (
    PurchaseOrder,
    StockBalance,
    StockCategory,
    StockItem,
    StockMovement,
    StockSupplier,
    StockTransfer,
)


@dataclass(frozen=True)
class StockItemFilter:
    category_id: int | None = None
    below_minimum_only: bool = False


class StockRepository(Protocol):
    async def list_categories(self) -> list[LookupItem]: ...

    async def list_suppliers(self) -> list[StockSupplierDto]: ...

    async def list_items(self, item_filter: StockItemFilter) -> list[StockItemDto]:
        """Active items only."""
        ...

    async def get_item_dto(self, item_id: int) -> StockItemDto | None: ...

    async def list_movements(self, item_id: int) -> list[StockMovementDto]: ...

    async def category_exists(self, category_id: int) -> bool: ...

    async def category_name_taken(self, name: str) -> bool: ...

    async def supplier_exists(self, supplier_id: int) -> bool: ...

    async def find_item(self, item_id: int) -> StockItem | None:
        """Loads an item for a change; save_changes stores it."""
        ...

    def add(self, entity: StockCategory | StockSupplier | StockItem | StockMovement) -> None: ...

    async def save_changes(self) -> None: ...


class PurchaseOrderRepository(Protocol):
    async def list_orders(self, status: str | None) -> list[PurchaseOrderSummaryDto]: ...

    async def get_dto(self, order_id: int) -> PurchaseOrderDto | None: ...

    async def find(self, order_id: int) -> PurchaseOrder | None:
        """The order with its lines, for a change."""
        ...

    async def on_order(self) -> dict[int, Decimal]:
        """How much of each consumable is ordered and not yet received (drafts and sent orders)."""
        ...

    async def find_items(self, item_ids: list[int]) -> list[StockItem]:
        """Active consumables, for changing their quantity (receiving)."""
        ...

    def add(self, entity: PurchaseOrder | StockMovement) -> None: ...

    async def save_changes(self) -> None: ...


class StockLocationRepository(Protocol):
    """Consumables at warehouses (erp_stock_location) and moves between them."""

    async def find_balances(self, item_id: int) -> list[StockBalance]:
        """The item's balances, for a change."""
        ...

    async def list_balances(self) -> list[StockBalanceRow]:
        """All non-zero balances, read only."""
        ...

    async def list_transfers(self, item_id: int) -> list[StockTransfer]: ...

    async def holds_stock(self, warehouse_id: int) -> bool: ...

    def add(self, entity: StockBalance | StockTransfer) -> None: ...
