from datetime import date, datetime

from sqlalchemy import delete, exists, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.domain.orders import OrderStatusChange
from newerp.infrastructure.cashbox.mapping import cheques
from newerp.infrastructure.delivery.mapping import shipment_documents
from newerp.infrastructure.models.customers import DdReturn
from newerp.infrastructure.models.marketing import DdVoucher
from newerp.infrastructure.models.orders import (
    DdOrder,
    DdOrderOption,
    DdOrderProduct,
    DdOrderStatus,
    DdOrderStatusLog,
    DdOrderTotal,
    ErpOrderManager,
)
from newerp.infrastructure.models.users import DdUser
from newerp.infrastructure.orders.credits import customer_reward, customer_transaction
from newerp.infrastructure.payments import bank_payments

# One statement, so two clicks at once cannot take the same number (MyISAM locks the table for it). MySQL refuses
# a subquery on the updated table itself, hence the derived table t.
_NEXT_INVOICE_NO = text(
    "UPDATE dd_order SET invoice_no = (SELECT n FROM (SELECT COALESCE(MAX(invoice_no), 0) + 1 AS n FROM dd_order "
    "WHERE invoice_prefix = :prefix) t) WHERE order_id = :id AND invoice_no = 0"
)


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

    async def save_shipping_date(self, order_id: int, day: date | None) -> None:
        await self._session.execute(update(DdOrder).where(DdOrder.order_id == order_id).values(date_shipping=day))
        await self._session.commit()

    async def create_invoice_no(self, order_id: int) -> str:
        prefix = await self._session.scalar(select(DdOrder.invoice_prefix).where(DdOrder.order_id == order_id))
        await self._session.execute(_NEXT_INVOICE_NO, {"id": order_id, "prefix": prefix})
        await self._session.commit()
        number = await self._session.scalar(select(DdOrder.invoice_no).where(DdOrder.order_id == order_id))
        return f"{prefix}{number}"

    async def delete_blockers(self, order_id: int) -> list[str]:
        checks = (
            ("є ТТН", exists().where(shipment_documents.c.order_id == order_id, shipment_documents.c.number != "")),
            ("є фіскальний чек", exists().where(cheques.c.order_id == order_id)),
            ("до нього прив'язана банківська оплата", exists().where(bank_payments.c.order_id == order_id)),
            ("є повернення", exists().where(DdReturn.order_id == order_id)),
            ("у ньому куплено подарунковий сертифікат", exists().where(DdVoucher.order_id == order_id)),
            (
                "його оплачено сертифікатом",
                exists().where(DdOrderTotal.order_id == order_id, DdOrderTotal.code == "voucher"),
            ),
        )
        return [reason for reason, check in checks if await self._session.scalar(select(check))]

    async def delete(self, order_id: int) -> None:
        # MyISAM: each delete stands alone. The order row goes last, so after a crash the order is still there
        # (without some lines) and can be deleted again.
        for table, column in (
            (DdOrderOption, DdOrderOption.order_id),
            (DdOrderProduct, DdOrderProduct.order_id),
            (DdOrderTotal, DdOrderTotal.order_id),
            (ErpOrderManager, ErpOrderManager.order_id),
            (customer_reward, customer_reward.c.order_id),
            (customer_transaction, customer_transaction.c.order_id),
            (DdOrder, DdOrder.order_id),
        ):
            await self._session.execute(delete(table).where(column == order_id))
        await self._session.commit()
