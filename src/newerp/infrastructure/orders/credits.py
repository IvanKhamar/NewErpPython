"""An order's reward points (dd_customer_reward) and partner commission (dd_customer_transaction: in OpenCart 3 a
partner is a customer, and the commission is a transaction on their balance)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, Table, Text, delete, exists, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.orders.credits import OrderCredits
from newerp.infrastructure.models.orders import DdOrder, DdOrderProduct

metadata = MetaData()
customer_reward = Table(
    "dd_customer_reward",
    metadata,
    Column("customer_reward_id", Integer, primary_key=True),
    Column("customer_id", Integer),
    Column("order_id", Integer),
    Column("description", Text),
    Column("points", Integer),
    Column("date_added", DateTime),
)
customer_transaction = Table(
    "dd_customer_transaction",
    metadata,
    Column("customer_transaction_id", Integer, primary_key=True),
    Column("customer_id", Integer),
    Column("order_id", Integer),
    Column("description", Text),
    Column("amount", Numeric(15, 4)),
    Column("date_added", DateTime),
)


class SqlOrderCreditStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, order_id: int) -> OrderCredits | None:
        r, t = customer_reward.c, customer_transaction.c
        row = (
            await self._session.execute(
                select(
                    DdOrder.customer_id,
                    select(func.coalesce(func.sum(DdOrderProduct.reward), 0))
                    .where(DdOrderProduct.order_id == order_id)
                    .scalar_subquery(),
                    exists().where(r.order_id == order_id, r.points > 0),
                    DdOrder.affiliate_id,
                    DdOrder.commission,
                    exists().where(t.order_id == order_id),
                ).where(DdOrder.order_id == order_id)
            )
        ).first()
        if row is None:
            return None
        return OrderCredits(
            customer_id=row[0],
            reward_points=int(row[1]),
            reward_added=bool(row[2]),
            affiliate_id=row[3],
            commission=row[4],
            commission_added=bool(row[5]),
        )

    async def add_reward(self, customer_id: int, order_id: int, points: int, description: str, now: datetime) -> None:
        await self._session.execute(
            insert(customer_reward).values(
                customer_id=customer_id, order_id=order_id, points=points, description=description, date_added=now
            )
        )
        await self._session.commit()

    async def remove_reward(self, order_id: int) -> None:
        r = customer_reward.c
        await self._session.execute(delete(customer_reward).where(r.order_id == order_id, r.points > 0))
        await self._session.commit()

    async def add_commission(
        self, affiliate_id: int, order_id: int, amount: Decimal, description: str, now: datetime
    ) -> None:
        await self._session.execute(
            insert(customer_transaction).values(
                customer_id=affiliate_id, order_id=order_id, amount=amount, description=description, date_added=now
            )
        )
        await self._session.commit()

    async def remove_commission(self, order_id: int) -> None:
        await self._session.execute(delete(customer_transaction).where(customer_transaction.c.order_id == order_id))
        await self._session.commit()
