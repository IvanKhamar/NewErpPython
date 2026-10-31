"""Sales by periods, like "Звіти → Продажі" of the old site."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import IntEnum


class ReportGrouping(IntEnum):
    """How to group sales in a report (the "Групувати за" filter of the old sales report)."""

    DAY = 0
    WEEK = 1
    MONTH = 2
    YEAR = 3


@dataclass(frozen=True)
class SaleFact:
    """One order for a report: its date, total and how many pieces of goods it has."""

    date_added: datetime
    total: Decimal
    products: int


@dataclass(frozen=True)
class SalesPeriod:
    """A report row: the period [start; end] and what was sold in it."""

    start: date
    end: date
    orders: int
    products: int
    total: Decimal


def period_start(day: date, grouping: ReportGrouping) -> date:
    """The first day of the period the date belongs to. A week starts on Monday, as in Ukraine (the old report used
    MySQL WEEK(), which counts from Sunday)."""
    match grouping:
        case ReportGrouping.DAY:
            return day
        case ReportGrouping.WEEK:
            return day - timedelta(days=day.weekday())
        case ReportGrouping.MONTH:
            return day.replace(day=1)
        case ReportGrouping.YEAR:
            return date(day.year, 1, 1)


def _next_period(start: date, grouping: ReportGrouping) -> date:
    match grouping:
        case ReportGrouping.DAY:
            return start + timedelta(days=1)
        case ReportGrouping.WEEK:
            return start + timedelta(days=7)
        case ReportGrouping.MONTH:
            return date(start.year + start.month // 12, start.month % 12 + 1, 1)
        case _:
            return date(start.year + 1, 1, 1)


def group_sales(
    facts: Iterable[SaleFact], grouping: ReportGrouping, from_: date, to: date, fill_gaps: bool
) -> list[SalesPeriod]:
    """Groups orders by periods within [from_; to]; the outer periods are clipped to from_ and to.
    The old report did not show empty periods; for a chart they can be added with zeros (fill_gaps)."""
    if to < from_:
        return []
    buckets: dict[date, list[SaleFact]] = defaultdict(list)
    for fact in facts:
        if from_ <= fact.date_added.date() <= to:
            buckets[period_start(fact.date_added.date(), grouping)].append(fact)

    rows = []
    start = period_start(from_, grouping)
    while start <= to:
        following = _next_period(start, grouping)
        clipped_start, clipped_end = max(start, from_), min(following - timedelta(days=1), to)
        if bucket := buckets.get(start):
            orders, products = len(bucket), sum(f.products for f in bucket)
            rows.append(
                SalesPeriod(clipped_start, clipped_end, orders, products, sum((f.total for f in bucket), Decimal(0)))
            )
        elif fill_gaps:
            rows.append(SalesPeriod(clipped_start, clipped_end, 0, 0, Decimal(0)))
        start = following
    return rows


def change_percent(current: Decimal, previous: Decimal) -> int | None:
    """Change in percent from the previous value; None when there is nothing to compare with (it was 0)."""
    if previous == 0:
        return None
    # ROUND_HALF_UP of Decimal rounds halves away from zero, like MidpointRounding.AwayFromZero.
    return int((Decimal(current - previous) * 100 / Decimal(previous)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
