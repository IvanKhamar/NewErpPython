"""dd_remarketing_orders of the storefront's remarketing module: UTM tags of orders and the times of pixel events."""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.remarketing import PIXEL_EVENTS, UTM_FIELDS, UtmFilter, UtmSale
from newerp.domain.reports import SaleFact
from newerp.infrastructure.models.orders import DdOrder, DdOrderProduct

remarketing_orders = Table(
    "dd_remarketing_orders",
    MetaData(),
    Column("order_id", Integer, primary_key=True),
    *(Column(name, String(255)) for name in ("first_referrer", "last_referrer", *UTM_FIELDS)),
    *(Column(name, DateTime) for name in PIXEL_EVENTS),
)
# A stored 0000-00-00 00:00:00 (the event was not sent) is below any real time; Python has no such datetime, so the
# query turns it into NULL.
FIRST_TIME = datetime(1000, 1, 1)


class SqlRemarketingStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def utm_sales(
        self, from_: datetime, to_exclusive: datetime, status_id: int | None, excluded_status_id: int, utm: UtmFilter
    ) -> list[UtmSale]:
        r, o = remarketing_orders.c, DdOrder
        conditions = [o.date_added >= from_, o.date_added < to_exclusive]
        if status_id is not None:
            conditions.append(o.order_status_id == status_id)
        else:
            conditions += [o.order_status_id > 0, o.order_status_id != excluded_status_id]
        for column, value in ((r.utm_source, utm.source), (r.utm_medium, utm.medium), (r.utm_campaign, utm.campaign)):
            if value:
                conditions.append(column == value)
        pieces = (
            select(func.coalesce(func.sum(DdOrderProduct.quantity), 0))
            .where(DdOrderProduct.order_id == o.order_id)
            .scalar_subquery()
        )
        rows = await self._session.execute(
            select(r.utm_source, o.date_added, o.total, pieces).join(o, o.order_id == r.order_id).where(*conditions)
        )
        return [
            UtmSale(source or "(без джерела)", SaleFact(added, total, int(count)))
            for source, added, total, count in rows
        ]

    async def utm_values(self, field: str) -> list[str]:
        column = remarketing_orders.c[field]
        rows = await self._session.scalars(select(column).where(column != "").distinct().order_by(column))
        return list(rows)

    async def order_row(self, order_id: int) -> dict[str, str | datetime | None] | None:
        r = remarketing_orders.c
        events = [case((r[name] < FIRST_TIME, None), else_=r[name]).label(name) for name in PIXEL_EVENTS]
        texts = [r[name] for name in ("first_referrer", "last_referrer", *UTM_FIELDS)]
        row = (await self._session.execute(select(*texts, *events).where(r.order_id == order_id))).mappings().first()
        return dict(row) if row is not None else None
