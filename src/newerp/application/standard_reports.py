"""OpenCart's standard reports (Python first, item 71) that the port did not have yet, as one table shape each
(title, columns, rows), so one page shows them all.

The marketing report is the tracking codes page (item 57), and the online, product views, rewards and taxes reports
came with item 31. Orders count when placed and not cancelled, as in the other reports of the port."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, Protocol

from newerp.application.common import Clock, Dto
from newerp.domain.common import DomainError

ReportKind = Literal[
    "sales-orders", "sales-returns", "sales-shipping", "sales-coupons", "products-purchased", "customer-orders",
    "customer-transactions", "customer-activity", "customer-searches", "statistics",
]  # fmt: skip
Grouping = Literal["day", "week", "month"]
MAX_DAYS = 366
MAX_ROWS = 500

TITLES: dict[str, tuple[str, list[str]]] = {
    "sales-orders": ("Продажі за період", ["Період", "Замовлень", "Товарів, шт.", "Сума, грн"]),
    "sales-returns": ("Повернення за період", ["Період", "Повернень"]),
    "sales-shipping": ("Доставка за період", ["Період", "Доставка", "Замовлень", "Сума, грн"]),
    "sales-coupons": ("Купони", ["Купон", "Код", "Замовлень", "Знижка, грн"]),
    "products-purchased": ("Куплені товари", ["Товар", "Модель", "Кількість", "Сума, грн"]),
    "customer-orders": ("Замовлення покупців", ["Покупець", "E-mail", "Замовлень", "Товарів, шт.", "Сума, грн"]),
    "customer-transactions": ("Баланс покупців", ["Покупець", "E-mail", "Сума, грн"]),
    "customer-activity": ("Дії покупців", ["Коли", "Покупець", "Дія", "IP"]),
    "customer-searches": ("Пошук на вітрині", ["Запит", "Пошуків", "Знайдено товарів (найбільше)", "Востаннє"]),
    "statistics": ("Лічильники OpenCart", ["Що", "Значення"]),
}

Cell = str | int | float


class ReportDto(Dto):
    kind: str
    title: str
    columns: list[str]
    rows: list[list[Cell]]
    date_from: date
    date_to: date
    cut: bool  # more rows than shown


class StandardReportStore(Protocol):
    async def rows(self, kind: ReportKind, date_from: date, date_to: date, grouping: Grouping) -> list[list[Cell]]:
        """Rows of the report for the days from–to (both included), at most MAX_ROWS + 1."""
        ...


class StandardReportService:
    def __init__(self, store: StandardReportStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def report(
        self, kind: ReportKind, date_from: date | None = None, date_to: date | None = None, grouping: Grouping = "day"
    ) -> ReportDto:
        end = date_to or self._clock.now().date()
        start = date_from or end - timedelta(days=29)
        if start > end:
            raise DomainError("Початок періоду пізніше за кінець.")
        if (end - start).days >= MAX_DAYS:
            raise DomainError(f"Період — не довше {MAX_DAYS} днів.")
        rows = [[_cell(v) for v in row] for row in await self._store.rows(kind, start, end, grouping)]
        title, columns = TITLES[kind]
        return ReportDto(kind=kind, title=title, columns=columns, rows=rows[:MAX_ROWS], date_from=start, date_to=end,
                         cut=len(rows) > MAX_ROWS)  # fmt: skip


def _cell(value: object) -> Cell:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, int | float | str):
        return value
    return "" if value is None else str(value)
