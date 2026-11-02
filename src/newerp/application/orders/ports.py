from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from newerp.application.common import LookupItem, PagedResult
from newerp.application.orders.models import (
    CustomerPickDto,
    NewOrderLineRecord,
    NewOrderRecord,
    OrderCompositionDto,
    OrderCreatedEvent,
    OrderDetails,
    OrderFilter,
    OrderListItem,
    OrderMethodsDto,
    OrderStatusDto,
    ProductPickDto,
)
from newerp.domain.catalog import SpecialPrice
from newerp.domain.orders import OrderDraft, OrderStatusChange


class OrderQueries(Protocol):
    async def list_orders(self, order_filter: OrderFilter) -> PagedResult[OrderListItem]: ...

    async def get(self, order_id: int) -> OrderDetails | None: ...

    async def list_statuses(self) -> list[OrderStatusDto]: ...

    async def list_sources(self) -> list[LookupItem]: ...

    async def list_managers(self) -> list[LookupItem]:
        """Who can be made responsible: active users of the old site."""
        ...


class OrderStore(Protocol):
    async def get_status_id(self, order_id: int) -> int | None:
        """The current status, or None if there is no such order."""
        ...

    async def status_exists(self, status_id: int) -> bool: ...

    async def is_active_user(self, user_id: int) -> bool: ...

    async def save_status_change(self, change: OrderStatusChange) -> None:
        """Updates the order's status and writes the change log."""
        ...

    async def save_manager(
        self, order_id: int, manager_id: int, assigned_by_user_id: int, assigned_at: datetime
    ) -> None: ...


class OrderStatusHandler(Protocol):
    """
    What other modules do when an order's status changes (for example, writing off consumables).
    OrderService calls every registered handler before saving the new status.
    """

    async def on_status_changing(self, change: OrderStatusChange) -> list[str]:
        """Warnings for the user (empty when all is well)."""
        ...


class NewOrderStore(Protocol):
    async def search_products(self, term: str, limit: int) -> list[ProductPickDto]: ...

    async def get_products(self, product_ids: list[int]) -> list[ProductPickDto]: ...

    async def order_ips(self, order_ids: list[int]) -> list[str]:
        """The non-empty IPs of these orders, the latest first (NewErp's own orders have none)."""
        ...

    async def orders_from_ip(self, ip: str, since: datetime) -> list[tuple[str, str, str]]:
        """(first name, last name, phone) of placed orders from this IP since then."""
        ...

    async def current_specials(self, product_ids: list[int], today: date) -> dict[int, SpecialPrice]:
        """The first sale price on today per product (by priority, then price), as the old site picked it."""
        ...

    async def search_customers(self, term: str, limit: int) -> list[CustomerPickDto]: ...

    async def find_customer_orders(
        self, phone_key: str | None, firstname: str | None, lastname: str | None, email: str | None, since: datetime
    ) -> list[tuple[int, int]]:
        """Recent (order id, status id) matching by phone (last 9 digits), by first and last name, or by email."""
        ...

    async def list_methods(self) -> OrderMethodsDto: ...

    async def source_exists(self, source_id: int) -> bool: ...

    async def create(self, order: NewOrderRecord) -> int:
        """Writes the order without a status (order_status_id = 0), its products, options and totals."""
        ...

    async def get_composition(self, order_id: int) -> OrderCompositionDto | None: ...

    async def replace_lines(
        self, order_id: int, draft: OrderDraft, lines: list[NewOrderLineRecord], now: datetime
    ) -> None: ...

    async def voucher_paid(self, order_id: int) -> Decimal:
        """How much of the order gift vouchers paid (positive), by its "voucher" total rows."""
        ...


class OrderEvents(Protocol):
    """Tells other systems what happened to orders (Kafka in production, a log locally)."""

    async def order_created(self, event: OrderCreatedEvent) -> None:
        """Called after the order is saved. Must not raise: the order exists whether the event went out or not."""
        ...

    async def order_status_changed(self, change: OrderStatusChange) -> None:
        """Called after a status change of an existing order is saved; must not raise either."""
        ...
