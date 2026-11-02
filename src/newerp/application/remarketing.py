"""Where orders come from (UTM tags and referrers) and when the storefront's pixels sent their events.

The storefront's "Remarketing All in One" module writes dd_remarketing_orders for each order it places; the pixels
themselves (GA4, Meta, TikTok…) live in the storefront and stay there. The ERP reads that table: the old site's UTM
report (admin/model/report/remarketing.php) and the block of the order card. Python first: .NET and Java have
neither."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol

from pydantic import Field

from newerp.application.common import Clock, Dto, Money, NotFoundError
from newerp.application.reports import MAX_DAYS, ReportFilter
from newerp.domain.common import DomainError
from newerp.domain.orders import CANCELLED_STATUS_ID
from newerp.domain.reports import SaleFact, group_sales

# The pixel events of the module, as its columns are named, with what the order card calls them.
PIXEL_EVENTS = {
    "success_page": "Сторінка «Дякуємо»",
    "ga4": "Google Analytics 4",
    "facebook": "Meta (Facebook) Purchase",
    "facebook_lead": "Meta (Facebook) Lead",
    "esputnik": "eSputnik",
    "telegram": "Telegram",
    "tiktok": "TikTok",
}
UTM_FIELDS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "utm_referrer")


@dataclass(frozen=True)
class UtmFilter:
    source: str | None = None
    medium: str | None = None
    campaign: str | None = None
    by_source: bool = False  # a row per period and source, as the old report's "group by utm"


@dataclass(frozen=True)
class UtmSale:
    source: str
    fact: SaleFact


class UtmReportRow(Dto):
    start: date
    end: date
    utm_source: str | None  # set when grouped by source
    orders: int
    products: int
    total: Money


class UtmReportDto(Dto):
    from_: date = Field(alias="from")
    to: date
    rows: list[UtmReportRow]
    orders: int
    total: Money
    sources: list[str]  # all sources seen, for the filter
    mediums: list[str]
    campaigns: list[str]


class PixelEventDto(Dto):
    name: str
    sent_at: datetime


class OrderRemarketingDto(Dto):
    """UTM tags and referrers of an order (only the filled ones) and the pixel events that were sent."""

    order_id: int
    utm: dict[str, str]
    events: list[PixelEventDto]


class RemarketingStore(Protocol):
    async def utm_sales(
        self, from_: datetime, to_exclusive: datetime, status_id: int | None, excluded_status_id: int, utm: UtmFilter
    ) -> list[UtmSale]:
        """Orders of the period that have a remarketing row, with their source."""
        ...

    async def utm_values(self, field: str) -> list[str]: ...

    async def order_row(self, order_id: int) -> dict[str, str | datetime | None] | None:
        """The order's row: the UTM fields as text, the events as a time or None (not sent)."""
        ...


class RemarketingService:
    def __init__(self, store: RemarketingStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def utm_report(self, report_filter: ReportFilter, utm: UtmFilter) -> UtmReportDto:
        today = self._clock.now().date()
        from_, to = report_filter.from_ or today.replace(day=1), report_filter.to or today
        if to < from_:
            raise DomainError("Дата «по» раніша за дату «з».")
        if (to - from_).days > MAX_DAYS:
            raise DomainError("Період звіту — не більше трьох років.")
        # The whole last day counts: up to the start of the next one.
        start, end = datetime.combine(from_, time.min), datetime.combine(to + timedelta(days=1), time.min)
        sales = await self._store.utm_sales(start, end, report_filter.status_id, CANCELLED_STATUS_ID, utm)
        groups: dict[str | None, list[SaleFact]] = defaultdict(list)
        for sale in sales:
            groups[sale.source if utm.by_source else None].append(sale.fact)
        rows = [
            UtmReportRow(
                start=p.start, end=p.end, utm_source=source, orders=p.orders, products=p.products, total=p.total
            )
            for source, facts in groups.items()
            for p in group_sales(facts, report_filter.group, from_, to, fill_gaps=False)
        ]
        rows.sort(key=lambda r: (r.start, r.utm_source or ""))
        return UtmReportDto(
            from_=from_, to=to, rows=rows, orders=sum(r.orders for r in rows),
            total=sum((r.total for r in rows), Decimal(0)), sources=await self._store.utm_values("utm_source"),
            mediums=await self._store.utm_values("utm_medium"), campaigns=await self._store.utm_values("utm_campaign"),
        )  # fmt: skip

    async def for_order(self, order_id: int) -> OrderRemarketingDto:
        row = await self._store.order_row(order_id)
        if row is None:
            raise NotFoundError(f"У замовлення {order_id} немає даних ремаркетингу.")
        utm = {
            name: str(row[name])
            for name in ("first_referrer", "last_referrer", *UTM_FIELDS)
            if isinstance(row.get(name), str) and row[name]
        }
        events = [
            PixelEventDto(name=label, sent_at=row[column])
            for column, label in PIXEL_EVENTS.items()
            if isinstance(row.get(column), datetime)
        ]
        return OrderRemarketingDto(order_id=order_id, utm=utm, events=events)
