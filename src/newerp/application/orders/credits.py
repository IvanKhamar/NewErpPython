"""An order's reward points for the customer and commission for the partner (affiliate), added and taken back by
hand, like the buttons of the old admin's order page. Money moves, so each change goes to the audit log."""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from newerp.application.common import Clock, Dto, Money, NotFoundError
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")


@dataclass(frozen=True)
class OrderCredits:
    customer_id: int
    reward_points: int  # the sum of the order lines' points, as OpenCart counts it
    reward_added: bool
    affiliate_id: int
    commission: Decimal
    commission_added: bool


class OrderCreditStore(Protocol):
    async def get(self, order_id: int) -> OrderCredits | None: ...

    async def add_reward(
        self, customer_id: int, order_id: int, points: int, description: str, now: datetime
    ) -> None: ...

    async def remove_reward(self, order_id: int) -> None:
        """Only the points the order gave (positive rows); points spent on it stay."""
        ...

    async def add_commission(
        self, affiliate_id: int, order_id: int, amount: Decimal, description: str, now: datetime
    ) -> None: ...

    async def remove_commission(self, order_id: int) -> None: ...


class OrderCreditsDto(Dto):
    order_id: int
    customer_id: int
    reward_points: int
    reward_added: bool
    affiliate_id: int
    commission: Money
    commission_added: bool


class OrderCreditService:
    def __init__(self, store: OrderCreditStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def get(self, order_id: int) -> OrderCreditsDto:
        credits = await self._credits(order_id)
        return OrderCreditsDto(order_id=order_id, **credits.__dict__)

    async def add_reward(self, order_id: int, user_id: int) -> OrderCreditsDto:
        """A second press adds nothing, like on the old site."""
        credits = await self._credits(order_id)
        if credits.customer_id == 0:
            raise DomainError("Замовлення оформлене без облікового запису клієнта: бонуси нікому нарахувати.")
        if credits.reward_points <= 0:
            raise DomainError("Товари замовлення не дають бонусних балів.")
        if not credits.reward_added:
            await self._store.add_reward(
                credits.customer_id, order_id, credits.reward_points, f"Замовлення №{order_id}", self._clock.now()
            )
            audit.info("reward added: order %s, customer %s, points %s, by user %s",
                       order_id, credits.customer_id, credits.reward_points, user_id)  # fmt: skip
        return await self.get(order_id)

    async def remove_reward(self, order_id: int, user_id: int) -> OrderCreditsDto:
        credits = await self._credits(order_id)
        if credits.reward_added:
            await self._store.remove_reward(order_id)
            audit.info("reward removed: order %s, customer %s, by user %s", order_id, credits.customer_id, user_id)
        return await self.get(order_id)

    async def add_commission(self, order_id: int, user_id: int) -> OrderCreditsDto:
        credits = await self._credits(order_id)
        if credits.affiliate_id == 0:
            raise DomainError("Замовлення прийшло не від партнера: комісію нікому нарахувати.")
        if credits.commission <= 0:
            raise DomainError("Комісія замовлення нульова.")
        if not credits.commission_added:
            await self._store.add_commission(
                credits.affiliate_id, order_id, credits.commission, f"Замовлення №{order_id}", self._clock.now()
            )
            audit.info("commission added: order %s, affiliate %s, amount %s, by user %s",
                       order_id, credits.affiliate_id, credits.commission, user_id)  # fmt: skip
        return await self.get(order_id)

    async def remove_commission(self, order_id: int, user_id: int) -> OrderCreditsDto:
        credits = await self._credits(order_id)
        if credits.commission_added:
            await self._store.remove_commission(order_id)
            audit.info(
                "commission removed: order %s, affiliate %s, by user %s", order_id, credits.affiliate_id, user_id
            )
        return await self.get(order_id)

    async def _credits(self, order_id: int) -> OrderCredits:
        credits = await self._store.get(order_id)
        if credits is None:
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")
        return credits
