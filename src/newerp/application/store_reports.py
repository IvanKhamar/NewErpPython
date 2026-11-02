"""Reports on what the storefront records and the ERP did not show: who is on the site now, product views, customers'
reward points and the taxes of orders (the old site's admin reports "Customers Online", "Products Viewed",
"Customer Reward Points" and "Tax"). Python first."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol

from pydantic import Field

from newerp.application.common import Clock, Dto, Money
from newerp.application.reports import MAX_DAYS, ReportFilter
from newerp.domain.common import DomainError
from newerp.domain.orders import CANCELLED_STATUS_ID
from newerp.domain.reports import SaleFact, group_sales

ONLINE_LIMIT = 200
VIEWS_LIMIT = 100
REWARDS_LIMIT = 200


class OnlineVisitorDto(Dto):
    ip: str
    customer_id: int  # 0: a guest
    customer: str | None
    url: str
    referer: str
    date_added: datetime


class ProductViewsDto(Dto):
    product_id: int
    name: str
    model: str
    viewed: int
    sold: int  # pieces in placed orders, all time
    percent: Money | None  # sold per 100 views; None without views


class CustomerRewardDto(Dto):
    customer_id: int
    customer: str | None
    points: int  # the balance: earned minus spent
    last_change: datetime


class TaxReportRow(Dto):
    start: date
    end: date
    title: str
    orders: int
    total: Money


class TaxReportDto(Dto):
    from_: date = Field(alias="from")
    to: date
    rows: list[TaxReportRow]
    total: Money


@dataclass(frozen=True)
class TaxFact:
    title: str
    date_added: datetime
    value: Decimal


class StoreReportStore(Protocol):
    async def online(self, limit: int) -> list[OnlineVisitorDto]: ...

    async def product_views(self, limit: int) -> list[tuple[int, str, str, int, int]]:
        """(product id, name, model, viewed, sold) of the most viewed enabled products."""
        ...

    async def rewards(self, limit: int) -> list[CustomerRewardDto]:
        """Customers with a non-zero balance, the largest first."""
        ...

    async def taxes(self, from_: datetime, to_exclusive: datetime, status_id: int | None, excluded_status_id: int
                    ) -> list[TaxFact]: ...  # fmt: skip


class StoreReportService:
    def __init__(self, store: StoreReportStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def online(self) -> list[OnlineVisitorDto]:
        return await self._store.online(ONLINE_LIMIT)

    async def product_views(self) -> list[ProductViewsDto]:
        return [
            ProductViewsDto(
                product_id=pid,
                name=name,
                model=model,
                viewed=viewed,
                sold=sold,
                percent=round(Decimal(sold) * 100 / viewed, 2) if viewed else None,
            )  # fmt: skip
            for pid, name, model, viewed, sold in await self._store.product_views(VIEWS_LIMIT)
        ]

    async def rewards(self) -> list[CustomerRewardDto]:
        return await self._store.rewards(REWARDS_LIMIT)

    async def taxes(self, report_filter: ReportFilter) -> TaxReportDto:
        """Tax lines of placed orders by period and tax name, as the old "Tax" report."""
        today = self._clock.now().date()
        from_, to = report_filter.from_ or today.replace(day=1), report_filter.to or today
        if to < from_:
            raise DomainError("Дата «по» раніша за дату «з».")
        if (to - from_).days > MAX_DAYS:
            raise DomainError("Період звіту — не більше трьох років.")
        start, end = datetime.combine(from_, time.min), datetime.combine(to + timedelta(days=1), time.min)
        facts = await self._store.taxes(start, end, report_filter.status_id, CANCELLED_STATUS_ID)
        by_title: dict[str, list[SaleFact]] = {}
        for fact in facts:
            by_title.setdefault(fact.title, []).append(SaleFact(fact.date_added, fact.value, 0))
        rows = [
            TaxReportRow(start=p.start, end=p.end, title=title, orders=p.orders, total=p.total)
            for title, title_facts in by_title.items()
            for p in group_sales(title_facts, report_filter.group, from_, to, fill_gaps=False)
        ]
        rows.sort(key=lambda r: (r.start, r.title))
        return TaxReportDto(from_=from_, to=to, rows=rows, total=sum((r.total for r in rows), Decimal(0)))
