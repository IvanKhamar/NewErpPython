"""Managers' bonuses for a period."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol

from pydantic import Field, computed_field

from newerp.application.common import Dto, Money
from newerp.domain.common import DomainError
from newerp.domain.staff import BonusOrder, BonusPolicy


class ManagerBonusRow(Dto):
    manager_id: int
    manager_name: str | None
    orders_count: int
    revenue: Money
    bonus: Money


class BonusReport(Dto):
    from_: date = Field(alias="from")  # "from" is a Python keyword
    to: date
    percent: Money
    managers: list[ManagerBonusRow]

    @computed_field
    @property
    def total_bonus(self) -> Money:
        return sum((m.bonus for m in self.managers), Decimal(0))


class BonusDataSource(Protocol):
    async def get_manager_orders(self, start: datetime, end_exclusive: datetime) -> list[BonusOrder]:
        """Orders with an assigned manager, created in [start, end_exclusive)."""
        ...

    async def get_user_names(self, user_ids: list[int]) -> dict[int, str]: ...


class BonusService:
    def __init__(self, source: BonusDataSource, percent: Decimal, counted_status_ids: list[int]) -> None:
        self._source = source
        self._percent = percent
        self._counted_status_ids = counted_status_ids

    async def calculate(self, from_: date, to: date) -> BonusReport:
        if to < from_:
            raise DomainError("Дата кінця періоду раніша за дату початку.")
        policy = BonusPolicy(self._percent, self._counted_status_ids)

        # The period includes both dates, so the upper bound is the day after "to".
        orders = await self._source.get_manager_orders(
            datetime.combine(from_, time.min), datetime.combine(to + timedelta(days=1), time.min)
        )
        bonuses = policy.calculate(orders)
        names = await self._source.get_user_names([b.manager_id for b in bonuses])
        rows = [
            ManagerBonusRow(
                manager_id=b.manager_id,
                manager_name=names.get(b.manager_id),
                orders_count=b.orders_count,
                revenue=b.revenue,
                bonus=b.bonus,
            )
            for b in bonuses
        ]
        return BonusReport(from_=from_, to=to, percent=policy.percent, managers=rows)
