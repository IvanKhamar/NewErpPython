"""Report queries over the OpenCart orders, returns and customers."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.reports import OrderStatusReportRow, ProductSalesRow, ReturnStatusReportRow
from newerp.domain.reports import SaleFact
from newerp.infrastructure.models.customers import DdCustomer, DdReturn, DdReturnStatus
from newerp.infrastructure.models.orders import DdOrder, DdOrderProduct, DdOrderStatus


class SqlReportStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def sales(
        self, from_: datetime, to_exclusive: datetime, status_id: int | None, excluded_status_id: int
    ) -> list[SaleFact]:
        orders = _orders(from_, to_exclusive, status_id, excluded_status_id)
        # Pieces are counted by one grouped query, not by a subquery for every order.
        p = DdOrderProduct
        quantities = await self._session.execute(
            select(p.order_id, func.sum(p.quantity))
            .join(DdOrder, DdOrder.order_id == p.order_id)
            .where(*orders)
            .group_by(p.order_id)
        )
        products = {order_id: int(quantity) for order_id, quantity in quantities}
        rows = await self._session.execute(select(DdOrder.order_id, DdOrder.date_added, DdOrder.total).where(*orders))
        return [SaleFact(added, total, products.get(order_id, 0)) for order_id, added, total in rows]

    async def products(
        self, from_: datetime, to_exclusive: datetime, status_id: int | None, excluded_status_id: int, limit: int
    ) -> list[ProductSalesRow]:
        p = DdOrderProduct
        quantity, total = func.sum(p.quantity), func.sum(p.total)
        rows = (
            await self._session.execute(
                select(p.product_id, quantity, total)
                .join(DdOrder, DdOrder.order_id == p.order_id)
                .where(*_orders(from_, to_exclusive, status_id, excluded_status_id))
                .group_by(p.product_id)
                .order_by(quantity.desc(), total.desc(), p.product_id)
                .limit(limit)
            )
        ).all()
        # The name and model come from the newest order line of the product (it may have been renamed).
        names: dict[int, tuple[str, str]] = {}
        if rows:
            lines = await self._session.execute(
                select(p.product_id, p.name, p.model)
                .where(p.product_id.in_([r[0] for r in rows]))
                .order_by(p.order_product_id.desc())
            )
            for product_id, name, model in lines:
                names.setdefault(product_id, (name, model))
        return [
            ProductSalesRow(
                product_id=product_id,
                name=names.get(product_id, ("", ""))[0],
                model=names.get(product_id, ("", ""))[1],
                quantity=int(sold),
                total=amount,
            )  # fmt: skip
            for product_id, sold, amount in rows
        ]

    async def new_customers(self, from_: datetime, to_exclusive: datetime) -> int:
        c = DdCustomer
        count = await self._session.scalar(
            select(func.count()).select_from(c).where(c.date_added >= from_, c.date_added < to_exclusive)
        )
        return count or 0

    async def orders_by_status(self, from_: datetime, to_exclusive: datetime) -> list[OrderStatusReportRow]:
        o = DdOrder
        groups = await self._session.execute(
            select(o.order_status_id, func.count(), func.sum(o.total))
            .where(o.date_added >= from_, o.date_added < to_exclusive, o.order_status_id > 0)
            .group_by(o.order_status_id)
            .order_by(o.order_status_id)
        )
        names = await self._names(DdOrderStatus.order_status_id, DdOrderStatus)
        return [
            OrderStatusReportRow(status_id=i, name=names.get(i, f"#{i}"), orders=count, total=total)
            for i, count, total in groups
        ]

    async def returns_by_status(self, from_: datetime, to_exclusive: datetime) -> list[ReturnStatusReportRow]:
        r = DdReturn
        groups = await self._session.execute(
            select(r.return_status_id, func.count(), func.sum(r.quantity))
            .where(r.date_added >= from_, r.date_added < to_exclusive)
            .group_by(r.return_status_id)
            .order_by(r.return_status_id)
        )
        names = await self._names(DdReturnStatus.return_status_id, DdReturnStatus)
        return [
            ReturnStatusReportRow(status_id=i, name=names.get(i, f"#{i}"), returns=count, quantity=int(quantity))
            for i, count, quantity in groups
        ]

    async def _names(self, id_column, model) -> dict[int, str]:
        """A status that is missing from the lookup is still shown, by its number."""
        rows = await self._session.execute(select(id_column, model.name).where(model.language_id == self._language_id))
        return {i: name for i, name in rows}


def _orders(from_: datetime, to_exclusive: datetime, status_id: int | None, excluded_status_id: int) -> list:
    o = DdOrder
    conditions = [o.date_added >= from_, o.date_added < to_exclusive]
    if status_id is not None:
        conditions.append(o.order_status_id == status_id)
    else:
        conditions += [o.order_status_id > 0, o.order_status_id != excluded_status_id]
    return conditions
