"""Queries over what the storefront records: dd_customer_online, dd_product.viewed, dd_customer_reward, tax totals."""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.store_reports import CustomerRewardDto, OnlineVisitorDto, TaxFact
from newerp.infrastructure.models.catalog import DdProduct, DdProductDescription
from newerp.infrastructure.models.customers import DdCustomer
from newerp.infrastructure.models.orders import DdOrder, DdOrderProduct, DdOrderTotal

metadata = MetaData()
customer_online = Table(
    "dd_customer_online",
    metadata,
    Column("ip", String(40), primary_key=True),
    Column("customer_id", Integer),
    Column("url", Text),
    Column("referer", Text),
    Column("date_added", DateTime),
)
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


def _name():
    return func.concat(DdCustomer.firstname, " ", DdCustomer.lastname)


class SqlStoreReportStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def online(self, limit: int) -> list[OnlineVisitorDto]:
        c = customer_online.c
        rows = await self._session.execute(
            select(c.ip, c.customer_id, _name(), c.url, c.referer, c.date_added)
            .outerjoin(DdCustomer, DdCustomer.customer_id == c.customer_id)
            .order_by(c.date_added.desc())
            .limit(limit)
        )
        return [
            OnlineVisitorDto(ip=ip, customer_id=cid, customer=name, url=url, referer=referer, date_added=added)
            for ip, cid, name, url, referer, added in rows
        ]

    async def product_views(self, limit: int) -> list[tuple[int, str, str, int, int]]:
        p, d = DdProduct, DdProductDescription
        sold = (
            select(func.coalesce(func.sum(DdOrderProduct.quantity), 0))
            .join(DdOrder, DdOrder.order_id == DdOrderProduct.order_id)
            .where(DdOrderProduct.product_id == p.product_id, DdOrder.order_status_id > 0)
            .scalar_subquery()
        )
        rows = await self._session.execute(
            select(p.product_id, d.name, p.model, p.viewed, sold)
            .join(d, (d.product_id == p.product_id) & (d.language_id == self._language_id))
            .where(p.status.is_(True), p.viewed > 0)
            .order_by(p.viewed.desc(), p.product_id)
            .limit(limit)
        )
        return [(pid, name, model, viewed, int(count)) for pid, name, model, viewed, count in rows]

    async def rewards(self, limit: int) -> list[CustomerRewardDto]:
        r = customer_reward.c
        points, last = func.sum(r.points), func.max(r.date_added)
        rows = await self._session.execute(
            select(r.customer_id, _name(), points, last)
            .outerjoin(DdCustomer, DdCustomer.customer_id == r.customer_id)
            .group_by(r.customer_id, DdCustomer.firstname, DdCustomer.lastname)
            .having(points != 0)
            .order_by(points.desc(), r.customer_id)
            .limit(limit)
        )
        return [
            CustomerRewardDto(customer_id=cid, customer=name, points=int(total), last_change=changed)
            for cid, name, total, changed in rows
        ]

    async def taxes(
        self, from_: datetime, to_exclusive: datetime, status_id: int | None, excluded_status_id: int
    ) -> list[TaxFact]:
        o, t = DdOrder, DdOrderTotal
        conditions = [o.date_added >= from_, o.date_added < to_exclusive, t.code == "tax"]
        if status_id is not None:
            conditions.append(o.order_status_id == status_id)
        else:
            conditions += [o.order_status_id > 0, o.order_status_id != excluded_status_id]
        rows = await self._session.execute(
            select(t.title, o.date_added, t.value).join(o, o.order_id == t.order_id).where(*conditions)
        )
        return [TaxFact(title, added, value) for title, added, value in rows]
