import re
from datetime import datetime, time, timedelta

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from newerp.application.common import LookupItem, PagedResult, normalize_paging, ukrainian_key
from newerp.application.orders.models import (
    OrderDetails,
    OrderFilter,
    OrderListItem,
    OrderProductLine,
    OrderStatusDto,
    OrderStatusHistoryEntry,
)
from newerp.infrastructure.delivery.mapping import shipment_documents
from newerp.infrastructure.models.orders import (
    DdOrder,
    DdOrderProduct,
    DdOrderSource,
    DdOrderStatus,
    DdOrderStatusLog,
    ErpOrderManager,
)
from newerp.infrastructure.models.users import DdUser

_INT32 = 2**31


def parse_int(text: str) -> int | None:
    """Like int.TryParse: digits with an optional sign, within the 32-bit range."""
    if not re.fullmatch(r"[+-]?\d+", text):
        return None
    value = int(text)
    return value if -_INT32 <= value < _INT32 else None


def full_name(user: type[DdUser]):
    return func.concat(user.firstname, " ", user.lastname)


class SqlOrderQueries:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def list_orders(self, order_filter: OrderFilter) -> PagedResult[OrderListItem]:
        f = order_filter
        page, page_size = normalize_paging(f.page, f.page_size)
        # OpenCart keeps unfinished checkouts with order_status_id = 0; they are not shown.
        conditions = [DdOrder.order_status_id > 0]
        if f.status_id is not None:
            conditions.append(DdOrder.order_status_id == f.status_id)
        if f.source_id is not None:
            conditions.append(DdOrder.order_source_id == f.source_id)
        if f.manager_id is not None:
            assigned = aliased(ErpOrderManager)  # the main query joins ErpOrderManager too
            conditions.append(exists().where(assigned.order_id == DdOrder.order_id, assigned.user_id == f.manager_id))
        if f.from_ is not None:
            conditions.append(DdOrder.date_added >= datetime.combine(f.from_, time.min))
        if f.to is not None:
            conditions.append(DdOrder.date_added < datetime.combine(f.to + timedelta(days=1), time.min))
        if f.search and f.search.strip():
            conditions.append(self._search(f.search.strip()))

        total = await self._session.scalar(select(func.count()).select_from(DdOrder).where(*conditions))

        # Left joins with the lookups: an order is shown even without a status or manager.
        status = select(DdOrderStatus).where(DdOrderStatus.language_id == self._language_id).subquery()
        source = select(DdOrderSource).where(DdOrderSource.language_id == self._language_id).subquery()
        rows = await self._session.execute(
            select(DdOrder, status.c.name, source.c.name, ErpOrderManager.user_id, full_name(DdUser))
            .outerjoin(status, status.c.order_status_id == DdOrder.order_status_id)
            .outerjoin(source, source.c.order_source_id == DdOrder.order_source_id)
            .outerjoin(ErpOrderManager, ErpOrderManager.order_id == DdOrder.order_id)
            .outerjoin(DdUser, DdUser.user_id == ErpOrderManager.user_id)
            .where(*conditions)
            .order_by(DdOrder.date_added.desc(), DdOrder.order_id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [
            OrderListItem(
                id=o.order_id,
                date_added=o.date_added,
                customer=f"{o.firstname} {o.lastname}",
                telephone=o.telephone,
                total=o.total,
                currency_code=o.currency_code,
                status_id=o.order_status_id,
                status_name=status_name,
                source_name=source_name,
                manager_id=manager_id,
                manager_name=manager_name,
            )
            for o, status_name, source_name, manager_id, manager_name in rows
        ]
        return PagedResult[OrderListItem](items=items, page=page, page_size=page_size, total_count=total or 0)

    @staticmethod
    def _search(s: str):
        if len(s) == 14 and s.isascii() and s.isdigit():
            # 14 digits: a Nova Poshta waybill number; find the order it belongs to.
            documents = shipment_documents.c
            return exists().where(documents.order_id == DdOrder.order_id, documents.number == s)
        order_id = parse_int(s)
        if order_id is not None:
            return or_(DdOrder.order_id == order_id, DdOrder.telephone.contains(s, autoescape=True))
        return or_(
            DdOrder.telephone.contains(s, autoescape=True),
            DdOrder.lastname.contains(s, autoescape=True),
            DdOrder.firstname.contains(s, autoescape=True),
            DdOrder.email.contains(s, autoescape=True),
        )

    async def get(self, order_id: int) -> OrderDetails | None:
        o = await self._session.get(DdOrder, order_id)
        if o is None:
            return None

        status_names = dict(
            (
                await self._session.execute(
                    select(DdOrderStatus.order_status_id, DdOrderStatus.name).where(
                        DdOrderStatus.language_id == self._language_id
                    )
                )
            ).all()
        )
        source_name = await self._session.scalar(
            select(DdOrderSource.name).where(
                DdOrderSource.order_source_id == o.order_source_id, DdOrderSource.language_id == self._language_id
            )
        )
        manager = (
            await self._session.execute(
                select(ErpOrderManager.user_id, full_name(DdUser))
                .join(DdUser, DdUser.user_id == ErpOrderManager.user_id)
                .where(ErpOrderManager.order_id == order_id)
            )
        ).first()
        products = await self._session.scalars(
            select(DdOrderProduct).where(DdOrderProduct.order_id == order_id).order_by(DdOrderProduct.order_product_id)
        )
        history = await self._session.execute(
            select(DdOrderStatusLog, full_name(DdUser))
            .outerjoin(DdUser, DdUser.user_id == DdOrderStatusLog.user_id)
            .where(DdOrderStatusLog.order_id == order_id)
            .order_by(DdOrderStatusLog.date_added, DdOrderStatusLog.order_status_log_id)
        )

        return OrderDetails(
            id=o.order_id,
            date_added=o.date_added,
            customer=f"{o.firstname} {o.lastname}",
            telephone=o.telephone,
            email=o.email,
            shipping_method=o.shipping_method,
            shipping_city=o.shipping_city,
            shipping_address=f"{o.shipping_address_1} {o.shipping_address_2}".strip(),
            payment_method=o.payment_method,
            comment=o.comment,
            total=o.total,
            currency_code=o.currency_code,
            status_id=o.order_status_id,
            status_name=status_names.get(o.order_status_id),
            source_id=o.order_source_id,
            source_name=source_name,
            manager_id=manager[0] if manager else None,
            manager_name=manager[1] if manager else None,
            products=[
                OrderProductLine(
                    product_id=p.product_id,
                    name=p.name,
                    model=p.model,
                    quantity=p.quantity,
                    price=p.price,
                    total=p.total,
                )
                for p in products
            ],
            status_history=[
                OrderStatusHistoryEntry(
                    changed_at=log.date_added,
                    old_status_id=log.old_status_id,
                    new_status_id=log.new_status_id,
                    new_status_name=status_names.get(log.new_status_id),
                    user_id=log.user_id,
                    user_name=user_name,
                )
                for log, user_name in history
            ],
        )

    async def list_statuses(self) -> list[OrderStatusDto]:
        rows = await self._session.scalars(
            select(DdOrderStatus)
            .where(DdOrderStatus.language_id == self._language_id)
            .order_by(DdOrderStatus.order_status_id)
        )
        return [OrderStatusDto(id=s.order_status_id, name=s.name, color=s.color) for s in rows]

    async def list_managers(self) -> list[LookupItem]:
        rows = await self._session.execute(
            select(DdUser.user_id, DdUser.firstname, DdUser.lastname).where(DdUser.status.is_(True))
        )
        managers = [LookupItem(id=user_id, name=f"{first} {last}".strip()) for user_id, first, last in rows]
        return sorted(managers, key=lambda m: ukrainian_key(m.name))

    async def list_sources(self) -> list[LookupItem]:
        rows = await self._session.scalars(
            select(DdOrderSource)
            .where(DdOrderSource.language_id == self._language_id)
            .order_by(DdOrderSource.order_source_id)
        )
        return [LookupItem(id=s.order_source_id, name=s.name) for s in rows]
