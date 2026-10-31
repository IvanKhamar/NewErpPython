"""The storefront product order by sales, like the old site's "Порядок товарів" module.

In every group (category, color, theme, label, cross-sell panel) products go by sales over a period, descending; the
ones pinned by hand take their positions. Before each change of sort_order a snapshot is saved, so the previous order
can come back.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Protocol

from pydantic import Field

from newerp.application.common import Clock, Dto, NotFoundError
from newerp.domain.catalog import SortGroup, SortGroupType, SortPin, by_sales, merge
from newerp.domain.common import DomainError

SEARCH_LIMIT = 15
SNAPSHOT_LIMIT = 50


@dataclass(frozen=True)
class ProductSortOptions:
    """Section ProductSort in .NET; the values are as in the old site's code."""

    counted_status_ids: list[int] = field(default_factory=list)  # orders whose sales count
    excluded_user_ids: list[int] = field(default_factory=list)  # orders made by these admin users (dd_order.user_id)
    # Orders with one of these in the name, surname, company or comment (wholesale, B2B) do not count.
    excluded_order_markers: list[str] = field(default_factory=list)
    excluded_comment_markers: list[str] = field(default_factory=list)  # ...or with one of these in the comment
    color_filter_ids: list[int] = field(default_factory=list)  # a category is ordered per color for these filters
    tag_filter_group_id: int = 0  # the "themes" filter group; each of its filters is a group
    new_label_name: str = "Новинка"  # this label goes newest first, not by sales
    default_days: int = 90


class SalesPeriod(Dto):
    """The last `days` days, or from `from` to `to` inclusive."""

    days: int | None = None
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None


class SortGroupDto(Dto):
    type: SortGroupType
    key: str
    name: str


class SortPreviewRow(Dto):
    """Where the product is now (among the group's products) and where it goes."""

    product_id: int
    name: str
    image: str
    sales: int
    pinned: bool
    current_position: int
    new_position: int


class ApplySortRequest(Dto):
    type: SortGroupType
    key: str
    product_ids: list[int] | None = None


class RecalculateGroupRequest(Dto):
    type: SortGroupType
    key: str
    period: SalesPeriod | None = None


class SortResult(Dto):
    """snapshot_id: the order before the change (to roll back); None for the cross-sell panel, which has its own."""

    snapshot_id: int | None
    updated: int


class SortPinDto(Dto):
    id: int
    type: SortGroupType
    key: str
    position: int
    product_id: int
    product_name: str


class SetPinRequest(Dto):
    type: SortGroupType
    key: str
    position: int
    product_id: int


class SortSnapshotDto(Dto):
    id: int
    note: str
    created_at: datetime


class SortProductDto(Dto):
    product_id: int
    name: str
    image: str
    sort_order: int


class CrossSellMetricDto(Dto):
    """What the cross-sell panel's popularity counts: all sales, or only those that came through the panel."""

    panel_only: bool


class ProductSortStore(Protocol):
    async def list_groups(self) -> list[SortGroup]:
        """All groups in recalculation order: themes, categories (colors apart), labels, the cross-sell panel."""
        ...

    async def group_product_ids(self, group: SortGroup) -> list[int]:
        """Enabled products of the group in the original order: a label newest first, the panel by its current
        order, the rest by id."""
        ...

    async def sales(
        self, product_ids: list[int], start: datetime, end: datetime, cross_sell_only: bool
    ) -> dict[int, int]:
        """Pieces sold per product in the period (counted orders only); cross_sell_only: added through the panel."""
        ...

    async def products(self, product_ids: list[int], cross_sell: bool) -> list[SortProductDto]:
        """Name, picture and current order (for the panel its own, 999999 without it)."""
        ...

    async def apply(self, cross_sell: bool, ordered_product_ids: list[int]) -> int:
        """Writes the order 1, 2, 3… to dd_product.sort_order or, for the panel, replaces dd_crosssell_sort."""
        ...

    async def pins(self, group: SortGroup) -> list[SortPin]: ...

    async def list_pins(self) -> list[SortPinDto]: ...

    async def set_pin(self, group: SortGroup, position: int, product_id: int, now: datetime) -> None:
        """The product's or the position's previous pin in the group goes away."""
        ...

    async def remove_pin(self, pin_id: int) -> bool: ...

    async def create_snapshot(self, note: str, now: datetime) -> int:
        """Saves the current sort_order of all products."""
        ...

    async def list_snapshots(self, limit: int) -> list[SortSnapshotDto]: ...

    async def restore_snapshot(self, snapshot_id: int) -> bool: ...

    async def cross_sell_panel_only(self) -> bool: ...

    async def set_cross_sell_panel_only(self, panel_only: bool) -> None: ...

    async def search_products(self, term: str, limit: int) -> list[SortProductDto]:
        """Enabled products with term in the name."""
        ...


class ProductSortService:
    def __init__(self, store: ProductSortStore, options: ProductSortOptions, clock: Clock) -> None:
        self._store = store
        self._options = options
        self._clock = clock

    async def list_groups(self) -> list[SortGroupDto]:
        return [SortGroupDto(type=g.type, key=g.key, name=g.name) for g in await self._store.list_groups()]

    async def preview(self, group_type: SortGroupType, key: str, period: SalesPeriod | None) -> list[SortPreviewRow]:
        """The group's new order without writing it: it can be viewed, changed by hand and written with apply()."""
        group = await self._find_group(group_type, key)
        order, sales, pinned = await self._calculate(group, self._to_range(period))
        if not order:
            return []
        products = {p.product_id: p for p in await self._store.products(order, group.type == SortGroupType.CROSS_SELL)}
        # The current position is the product's place among the same group's products by their sort_order now.
        by_current = sorted(order, key=lambda i: (products[i].sort_order if i in products else 0, i))
        current = {product_id: index + 1 for index, product_id in enumerate(by_current)}
        rows = []
        for index, product_id in enumerate(order):
            product = products.get(product_id)
            rows.append(
                SortPreviewRow(
                    product_id=product_id,
                    name=product.name if product else "(товар видалено)",
                    image=product.image if product else "",
                    sales=sales.get(product_id, 0),
                    pinned=product_id in pinned,
                    current_position=current[product_id],
                    new_position=index + 1,
                )
            )
        return rows

    async def apply(self, request: ApplySortRequest) -> SortResult:
        """Writes a ready order of a group (for example, a preview changed by hand)."""
        group = await self._find_group(request.type, request.key)
        ids = request.product_ids or []
        if not ids:
            raise DomainError("Немає товарів для запису — спершу переглянь новий порядок.")
        if len(set(ids)) != len(ids):
            raise DomainError("Товар у порядку повторюється.")
        snapshot = await self._snapshot_unless_cross_sell(group, f"Порядок групи: {group.name}")
        return SortResult(
            snapshot_id=snapshot, updated=await self._store.apply(group.type == SortGroupType.CROSS_SELL, ids)
        )

    async def recalculate_group(self, request: RecalculateGroupRequest) -> SortResult:
        group = await self._find_group(request.type, request.key)
        start_end = self._to_range(request.period)
        snapshot = await self._snapshot_unless_cross_sell(group, f"Перерахунок групи: {group.name}")
        order, _, _ = await self._calculate(group, start_end)
        updated = await self._store.apply(group.type == SortGroupType.CROSS_SELL, order) if order else 0
        return SortResult(snapshot_id=snapshot, updated=updated)

    async def recalculate_all(self, period: SalesPeriod | None) -> SortResult:
        """Recalculates every group in turn (themes, categories, labels, the panel), one snapshot before the start.

        A product has one sort_order, so a product of several groups gets the position from the last of them, as on
        the old site.
        """
        start_end = self._to_range(period)
        snapshot = await self._store.create_snapshot("Перед перерахунком усіх груп", self._clock.now())
        updated = 0
        for group in await self._store.list_groups():
            order, _, _ = await self._calculate(group, start_end)
            if order:
                updated += await self._store.apply(group.type == SortGroupType.CROSS_SELL, order)
        return SortResult(snapshot_id=snapshot, updated=updated)

    async def list_snapshots(self) -> list[SortSnapshotDto]:
        return await self._store.list_snapshots(SNAPSHOT_LIMIT)

    async def restore_snapshot(self, snapshot_id: int) -> None:
        if not await self._store.restore_snapshot(snapshot_id):
            raise NotFoundError(f"Знімка {snapshot_id} не знайдено.")

    async def list_pins(self) -> list[SortPinDto]:
        return await self._store.list_pins()

    async def set_pin(self, request: SetPinRequest) -> None:
        if request.position < 1:
            raise DomainError("Позиція рахується з 1.")
        group = await self._find_group(request.type, request.key)
        if not await self._store.products([request.product_id], False):
            raise DomainError(f"Товару {request.product_id} не знайдено.")
        await self._store.set_pin(group, request.position, request.product_id, self._clock.now())

    async def remove_pin(self, pin_id: int) -> None:
        if not await self._store.remove_pin(pin_id):
            raise NotFoundError(f"Закріплення {pin_id} не знайдено.")

    async def get_cross_sell_metric(self) -> CrossSellMetricDto:
        return CrossSellMetricDto(panel_only=await self._store.cross_sell_panel_only())

    async def set_cross_sell_metric(self, metric: CrossSellMetricDto) -> CrossSellMetricDto:
        await self._store.set_cross_sell_panel_only(metric.panel_only)
        return metric

    async def search_products(self, term: str | None) -> list[SortProductDto]:
        if not term or len(term.strip()) < 2:
            return []
        return await self._store.search_products(term.strip(), SEARCH_LIMIT)

    async def _calculate(
        self, group: SortGroup, start_end: tuple[datetime, datetime]
    ) -> tuple[list[int], dict[int, int], set[int]]:
        product_ids = await self._store.group_product_ids(group)
        if not product_ids:
            return [], {}, set()
        cross_sell_only = group.type == SortGroupType.CROSS_SELL and await self._store.cross_sell_panel_only()
        sales = await self._store.sales(product_ids, *start_end, cross_sell_only)
        # The "Новинка" label stays newest first, without sales.
        is_new = group.type == SortGroupType.LABEL and group.key.casefold() == self._options.new_label_name.casefold()
        queue = product_ids if is_new else by_sales(product_ids, sales)
        pins = await self._store.pins(group)
        return merge(queue, pins), sales, {pin.product_id for pin in pins}

    async def _find_group(self, group_type: SortGroupType, key: str) -> SortGroup:
        for group in await self._store.list_groups():
            if group.type == group_type and group.key == key:
                return group
        raise NotFoundError(f"Групи «{key}» немає (у ній мають бути увімкнені товари).")

    async def _snapshot_unless_cross_sell(self, group: SortGroup, note: str) -> int | None:
        if group.type == SortGroupType.CROSS_SELL:
            return None
        return await self._store.create_snapshot(note, self._clock.now())

    def _to_range(self, period: SalesPeriod | None) -> tuple[datetime, datetime]:
        now = self._clock.now()
        if period is not None and period.from_ is not None and period.to is not None:
            if period.from_ > period.to:
                raise DomainError("Початок періоду пізніший за кінець.")
            return datetime.combine(period.from_, time.min), datetime.combine(period.to, time.max)
        if period is not None and (period.from_ is not None or period.to is not None):
            raise DomainError("Вкажи обидві дати періоду.")
        days = period.days if period is not None and period.days and period.days > 0 else self._options.default_days
        return now - timedelta(days=days), now
