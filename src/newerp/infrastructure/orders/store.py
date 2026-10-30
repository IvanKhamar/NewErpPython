from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.domain.orders import OrderStatusChange
from newerp.infrastructure.models.orders import DdOrder, DdOrderStatus, DdOrderStatusLog, ErpOrderManager
from newerp.infrastructure.models.users import DdUser


class SqlOrderStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_status_id(self, order_id: int) -> int | None:
        return await self._session.scalar(select(DdOrder.order_status_id).where(DdOrder.order_id == order_id))

    async def status_exists(self, status_id: int) -> bool:
        return bool(await self._session.scalar(select(exists().where(DdOrderStatus.order_status_id == status_id))))

    async def is_active_user(self, user_id: int) -> bool:
        return bool(
            await self._session.scalar(select(exists().where(DdUser.user_id == user_id, DdUser.status.is_(True))))
        )

    async def save_status_change(self, change: OrderStatusChange) -> None:
        # The OpenCart tables are MyISAM, which has no transactions: the update and the log row are not atomic,
        # so after a crash between them the log may miss a row.
        order = await self._session.get_one(DdOrder, change.order_id)
        order.order_status_id = change.new_status_id
        order.date_modified = change.changed_at
        self._session.add(
            DdOrderStatusLog(
                order_id=change.order_id,
                user_id=change.user_id,
                old_status_id=change.old_status_id,
                new_status_id=change.new_status_id,
                date_added=change.changed_at,
            )
        )
        await self._session.commit()

    async def save_manager(
        self, order_id: int, manager_id: int, assigned_by_user_id: int, assigned_at: datetime
    ) -> None:
        row = await self._session.get(ErpOrderManager, order_id)
        if row is None:
            row = ErpOrderManager(order_id=order_id)
            self._session.add(row)
        row.user_id = manager_id
        row.assigned_by_user_id = assigned_by_user_id
        row.assigned_at = assigned_at
        await self._session.commit()
