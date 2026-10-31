"""The home page summary and the sales and products reports, like the home page and "Звіти" of the old site.
Cancelled orders ("Відмінено") are not counted unless that status is chosen; the old site counted them."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol

from pydantic import Field

from newerp.application.common import Clock, Dto, Money
from newerp.domain.common import DomainError
from newerp.domain.orders import CANCELLED_STATUS_ID
from newerp.domain.reports import ReportGrouping, SaleFact, SalesPeriod, change_percent, group_sales

MAX_DAYS = 3 * 366
MAX_PRODUCTS = 200


@dataclass(frozen=True)
class ReportFilter:
    """Without a status every placed order counts, except cancelled ones."""

    from_: date | None = None
    to: date | None = None
    status_id: int | None = None
    group: ReportGrouping = ReportGrouping.WEEK


class SalesReportRow(Dto):
    start: date
    end: date
    orders: int
    products: int
    total: Money


class SalesReportDto(Dto):
    from_: date = Field(alias="from")
    to: date
    rows: list[SalesReportRow]
    orders: int
    products: int
    total: Money


class ProductSalesRow(Dto):
    product_id: int
    name: str
    model: str
    quantity: int
    total: Money


class ProductSalesDto(Dto):
    from_: date = Field(alias="from")
    to: date
    rows: list[ProductSalesRow]


class OrderStatusReportRow(Dto):
    """Orders of one status in the period: how many and their sum."""

    status_id: int
    name: str
    orders: int
    total: Money


class ReturnStatusReportRow(Dto):
    """Returns of one status in the period: how many returns and how many pieces."""

    status_id: int
    name: str
    returns: int
    quantity: int


class StatusReportDto(Dto):
    """Orders and returns by status, like "Статистика" of the old site. Every status is its own row, so cancelled
    orders count too; unfinished ones (status 0) do not."""

    from_: date = Field(alias="from")
    to: date
    orders: list[OrderStatusReportRow]
    order_count: int
    total: Money
    returns: list[ReturnStatusReportRow]
    return_count: int


class DashboardTile(Dto):
    """A "now against before" figure; change_percent is None when before was 0."""

    current: Money
    previous: Money
    change_percent: int | None


class DashboardDto(Dto):
    orders_today: DashboardTile
    sales_today: DashboardTile
    orders_month: DashboardTile
    sales_month: DashboardTile
    average_check_month: DashboardTile
    new_customers_month: DashboardTile
    last30_days: list[SalesReportRow]
    top_products30_days: list[ProductSalesRow]


class RevenueTotals(Dto):
    from_: date = Field(alias="from")
    to: date
    orders: int
    revenue: Money
    average_check: Money


class RevenueDay(Dto):
    day: date = Field(alias="date")
    orders: int
    products: int
    revenue: Money


class RevenueFeedDto(Dto):
    """Revenue for an outside dashboard: placed orders without cancelled ones, as on the home page. previous_month
    is the same days of the month before (1–24 September against 1–24 October). days go from the oldest to today."""

    generated_at: datetime
    currency: str
    today: RevenueTotals
    yesterday: RevenueTotals
    month: RevenueTotals
    previous_month: RevenueTotals
    year: RevenueTotals
    days: list[RevenueDay]


class ReportStore(Protocol):
    async def sales(
        self, from_: datetime, to_exclusive: datetime, status_id: int | None, excluded_status_id: int
    ) -> list[SaleFact]:
        """Orders dated in [from_; to_exclusive): with status_id or, without it, any placed one except
        excluded_status_id."""
        ...

    async def products(
        self, from_: datetime, to_exclusive: datetime, status_id: int | None, excluded_status_id: int, limit: int
    ) -> list[ProductSalesRow]:
        """Products by the quantity sold, with the same conditions on orders."""
        ...

    async def new_customers(self, from_: datetime, to_exclusive: datetime) -> int: ...

    async def orders_by_status(self, from_: datetime, to_exclusive: datetime) -> list[OrderStatusReportRow]:
        """Placed orders dated in [from_; to_exclusive) by status, cancelled ones too."""
        ...

    async def returns_by_status(self, from_: datetime, to_exclusive: datetime) -> list[ReturnStatusReportRow]:
        """Returns added in [from_; to_exclusive) by status."""
        ...


class ReportService:
    def __init__(self, store: ReportStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def sales(self, report_filter: ReportFilter) -> SalesReportDto:
        from_, to = self._range(report_filter)
        facts = await self._store.sales(_start(from_), _end(to), report_filter.status_id, CANCELLED_STATUS_ID)
        rows = [_row(p) for p in group_sales(facts, report_filter.group, from_, to, fill_gaps=False)]
        return SalesReportDto(
            from_=from_, to=to, rows=rows, orders=sum(r.orders for r in rows), products=sum(r.products for r in rows),
            total=sum((r.total for r in rows), Decimal(0)),
        )  # fmt: skip

    async def products(self, report_filter: ReportFilter) -> ProductSalesDto:
        from_, to = self._range(report_filter)
        rows = await self._store.products(
            _start(from_), _end(to), report_filter.status_id, CANCELLED_STATUS_ID, MAX_PRODUCTS
        )
        return ProductSalesDto(from_=from_, to=to, rows=rows)

    async def statuses(self, report_filter: ReportFilter) -> StatusReportDto:
        """Orders and returns by status; status_id and group of the filter do not apply here."""
        from_, to = self._range(report_filter)
        orders = await self._store.orders_by_status(_start(from_), _end(to))
        returns = await self._store.returns_by_status(_start(from_), _end(to))
        return StatusReportDto(
            from_=from_, to=to, orders=orders, order_count=sum(r.orders for r in orders),
            total=sum((r.total for r in orders), Decimal(0)), returns=returns,
            return_count=sum(r.returns for r in returns),
        )  # fmt: skip

    async def dashboard(self) -> DashboardDto:
        today = self._today()
        month_start, prev_month_start, prev_month_end = _months(today)
        chart_from = today - timedelta(days=29)

        facts = await self._store.sales(
            _start(min(prev_month_start, chart_from)), _end(today), None, CANCELLED_STATUS_ID
        )
        day = _totals(facts, today, today)
        yesterday = _totals(facts, today - timedelta(days=1), today - timedelta(days=1))
        month = _totals(facts, month_start, today)
        prev_month = _totals(facts, prev_month_start, prev_month_end)
        customers = await self._store.new_customers(_start(month_start), _end(today))
        prev_customers = await self._store.new_customers(_start(prev_month_start), _end(prev_month_end))
        top = await self._store.products(_start(chart_from), _end(today), None, CANCELLED_STATUS_ID, 5)

        return DashboardDto(
            orders_today=_tile(day[0], yesterday[0]),
            sales_today=_tile(day[1], yesterday[1]),
            orders_month=_tile(month[0], prev_month[0]),
            sales_month=_tile(month[1], prev_month[1]),
            average_check_month=_tile(_average(month), _average(prev_month)),
            new_customers_month=_tile(customers, prev_customers),
            last30_days=[_row(p) for p in group_sales(facts, ReportGrouping.DAY, chart_from, today, fill_gaps=True)],
            top_products30_days=top,
        )

    async def revenue_feed(self, days: int, max_days: int) -> RevenueFeedDto:
        """Today, yesterday, the month and the year to date, and the last `days` days one by one."""
        if not 1 <= days <= max_days:
            raise DomainError(f"Кількість днів — від 1 до {max_days}.")
        today = self._today()
        month_start, prev_month_start, prev_month_end = _months(today)
        year_start = date(today.year, 1, 1)
        series_from = today - timedelta(days=days - 1)

        facts = await self._store.sales(
            _start(min(prev_month_start, year_start, series_from)), _end(today), None, CANCELLED_STATUS_ID
        )

        def feed(start: date, end: date) -> RevenueTotals:
            totals = _totals(facts, start, end)
            return RevenueTotals(
                from_=start, to=end, orders=totals[0], revenue=totals[1], average_check=_average(totals)
            )

        series = [
            RevenueDay(day=p.start, orders=p.orders, products=p.products, revenue=p.total)
            for p in group_sales(facts, ReportGrouping.DAY, series_from, today, fill_gaps=True)
        ]
        yesterday = today - timedelta(days=1)
        return RevenueFeedDto(
            generated_at=self._clock.now(), currency="UAH", today=feed(today, today),
            yesterday=feed(yesterday, yesterday), month=feed(month_start, today),
            previous_month=feed(prev_month_start, prev_month_end), year=feed(year_start, today), days=series,
        )  # fmt: skip

    def _range(self, report_filter: ReportFilter) -> tuple[date, date]:
        today = self._today()
        # By default the current month, as in the old reports.
        from_ = report_filter.from_ or today.replace(day=1)
        to = report_filter.to or today
        if to < from_:
            raise DomainError("Дата «по» раніша за дату «з».")
        if (to - from_).days > MAX_DAYS:
            raise DomainError("Період звіту — не більше трьох років.")
        return from_, to

    def _today(self) -> date:
        return self._clock.now().date()


def _months(today: date) -> tuple[date, date, date]:
    """The month start, and the same days of the month before: 1–20 September for 1–20 October."""
    month_start = today.replace(day=1)
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    prev_month_end = min(prev_month_start + timedelta(days=today.day - 1), month_start - timedelta(days=1))
    return month_start, prev_month_start, prev_month_end


def _totals(facts: list[SaleFact], from_: date, to: date) -> tuple[int, Decimal]:
    inside = [f.total for f in facts if from_ <= f.date_added.date() <= to]
    return len(inside), sum(inside, Decimal(0))


def _average(totals: tuple[int, Decimal]) -> Decimal:
    orders, total = totals
    # quantize rounds half to even by default, like Math.Round in .NET.
    return Decimal(0) if orders == 0 else (total / orders).quantize(Decimal("0.01"))


def _tile(current: Decimal | int, previous: Decimal | int) -> DashboardTile:
    return DashboardTile(
        current=current, previous=previous, change_percent=change_percent(Decimal(current), Decimal(previous))
    )


def _row(period: SalesPeriod) -> SalesReportRow:
    return SalesReportRow(
        start=period.start, end=period.end, orders=period.orders, products=period.products, total=period.total
    )


def _start(day: date) -> datetime:
    return datetime.combine(day, time.min)


def _end(day: date) -> datetime:
    """The start of the next day: the exclusive end of a range that includes `day`."""
    return _start(day + timedelta(days=1))
