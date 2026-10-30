from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.domain.staff import BonusOrder
from newerp.infrastructure.models.orders import DdOrder, ErpOrderManager
from newerp.infrastructure.models.users import DdUser


class SqlBonusDataSource:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_manager_orders(self, start: datetime, end_exclusive: datetime) -> list[BonusOrder]:
        rows = await self._session.execute(
            select(DdOrder.order_id, ErpOrderManager.user_id, DdOrder.order_status_id, DdOrder.total)
            .join(ErpOrderManager, ErpOrderManager.order_id == DdOrder.order_id)
            .where(DdOrder.date_added >= start, DdOrder.date_added < end_exclusive)
        )
        return [BonusOrder(*row) for row in rows]

    async def get_user_names(self, user_ids: list[int]) -> dict[int, str]:
        if not user_ids:
            return {}
        rows = await self._session.execute(
            select(DdUser.user_id, DdUser.firstname, DdUser.lastname).where(DdUser.user_id.in_(user_ids))
        )
        return {user_id: f"{first} {last}" for user_id, first, last in rows}
