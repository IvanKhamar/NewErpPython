from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from newerp.domain.common import DomainError


@dataclass(frozen=True)
class BonusOrder:
    """A manager's order that may give a bonus."""

    order_id: int
    manager_id: int
    status_id: int
    total: Decimal


@dataclass(frozen=True)
class ManagerBonus:
    manager_id: int
    orders_count: int
    revenue: Decimal
    bonus: Decimal


class BonusPolicy:
    """
    A percent of the sum of orders in the "counted" statuses. This is our simplified rule; the exact formula of the
    old user/bonus module still has to be checked.
    """

    def __init__(self, percent: Decimal, counted_status_ids: Iterable[int]) -> None:
        if not 0 <= percent <= 100:
            raise DomainError("Відсоток бонусу має бути від 0 до 100.")
        self.percent = percent
        self.counted_status_ids = frozenset(counted_status_ids)
        if not self.counted_status_ids:
            raise DomainError("Вкажіть хоча б один статус, за який нараховується бонус.")

    def calculate(self, orders: Iterable[BonusOrder]) -> list[ManagerBonus]:
        by_manager: dict[int, list[Decimal]] = defaultdict(list)
        for order in orders:
            if order.status_id in self.counted_status_ids:
                by_manager[order.manager_id].append(order.total)

        bonuses = []
        for manager_id, totals in by_manager.items():
            revenue = sum(totals, Decimal(0))
            bonus = (revenue * self.percent / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
            bonuses.append(ManagerBonus(manager_id, len(totals), revenue, bonus))
        return sorted(bonuses, key=lambda b: b.bonus, reverse=True)
