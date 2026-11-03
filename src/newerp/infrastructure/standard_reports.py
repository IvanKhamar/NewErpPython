"""The queries of OpenCart's standard reports over the order, return, coupon, customer and storefront tables."""

from datetime import date, datetime, time, timedelta

from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, String, Table, Text, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.standard_reports import MAX_ROWS, Cell, Grouping, ReportKind
from newerp.domain.orders import CANCELLED_STATUS_ID
from newerp.infrastructure.models.customers import DdCustomer, DdReturn
from newerp.infrastructure.models.marketing import DdCoupon, DdCouponHistory
from newerp.infrastructure.models.orders import DdOrder, DdOrderProduct, DdOrderTotal
from newerp.infrastructure.orders.credits import customer_transaction

metadata = MetaData()
customer_activity = Table(
    "dd_customer_activity", metadata, Column("customer_activity_id", Integer, primary_key=True),
    Column("customer_id", Integer), Column("key", String(64)), Column("data", Text), Column("ip", String(40)),
    Column("date_added", DateTime),
)  # fmt: skip
customer_search = Table(
    "dd_customer_search", metadata, Column("customer_search_id", Integer, primary_key=True),
    Column("customer_id", Integer), Column("keyword", String(255)), Column("products", Integer),
    Column("date_added", DateTime),
)  # fmt: skip
statistics = Table(
    "dd_statistics", metadata, Column("statistics_id", Integer, primary_key=True), Column("code", String(64)),
    Column("value", Numeric(15, 4)),
)  # fmt: skip

LIMIT = MAX_ROWS + 1


def _period(column, grouping: Grouping):
    """The first day of the column's day, ISO week or month, as text."""
    if grouping == "month":
        return func.date_format(column, "%Y-%m-01")
    if grouping == "week":
        return func.date_format(func.subdate(func.date(column), func.weekday(column)), "%Y-%m-%d")  # the Monday
    return func.date_format(column, "%Y-%m-%d")


class SqlStandardReportStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def rows(self, kind: ReportKind, date_from: date, date_to: date, grouping: Grouping) -> list[list[Cell]]:
        start, end = datetime.combine(date_from, time.min), datetime.combine(date_to + timedelta(days=1), time.min)
        query = getattr(self, "_" + kind.replace("-", "_"))(start, end, grouping)
        return [list(row) for row in await self._session.execute(query.limit(LIMIT))]

    @staticmethod
    def _placed(start: datetime, end: datetime):
        o = DdOrder
        return (
            o.order_status_id > 0,
            o.order_status_id != CANCELLED_STATUS_ID,
            o.date_added >= start,
            o.date_added < end,
        )

    def _sales_orders(self, start, end, grouping):
        o, p = DdOrder, DdOrderProduct
        period = _period(o.date_added, grouping)
        products = select(func.coalesce(func.sum(p.quantity), 0)).where(p.order_id == o.order_id).scalar_subquery()
        inner = (
            select(period.label("period"), o.order_id, products.label("products"), o.total)
            .where(*self._placed(start, end))
            .subquery()
        )
        return (
            select(inner.c.period, func.count(), func.sum(inner.c.products), func.sum(inner.c.total))
            .group_by(inner.c.period)
            .order_by(inner.c.period)
        )

    def _sales_returns(self, start, end, grouping):
        r = DdReturn
        period = _period(r.date_added, grouping)
        return (
            select(period, func.count())
            .where(r.date_added >= start, r.date_added < end)
            .group_by(period)
            .order_by(period)
        )

    def _sales_shipping(self, start, end, grouping):
        o, t = DdOrder, DdOrderTotal
        period = _period(o.date_added, grouping)
        return (
            select(period, t.title, func.count(), func.sum(t.value))
            .join(t, (t.order_id == o.order_id) & (t.code == "shipping"))
            .where(*self._placed(start, end))
            .group_by(period, t.title)
            .order_by(period, t.title)
        )

    def _sales_coupons(self, start, end, grouping):
        h, c = DdCouponHistory, DdCoupon
        return (
            select(c.name, c.code, func.count(func.distinct(h.order_id)), -func.sum(h.amount))
            .join(c, c.coupon_id == h.coupon_id)
            .where(h.date_added >= start, h.date_added < end)
            .group_by(c.coupon_id, c.name, c.code)
            .order_by(func.count(func.distinct(h.order_id)).desc())
        )

    def _products_purchased(self, start, end, grouping):
        o, p = DdOrder, DdOrderProduct
        return (
            select(p.name, p.model, func.sum(p.quantity), func.sum(p.total))
            .join(o, o.order_id == p.order_id)
            .where(*self._placed(start, end))
            .group_by(p.product_id, p.name, p.model)
            .order_by(func.sum(p.quantity).desc())
        )

    def _customer_orders(self, start, end, grouping):
        o, p, c = DdOrder, DdOrderProduct, DdCustomer
        products = select(func.coalesce(func.sum(p.quantity), 0)).where(p.order_id == o.order_id).scalar_subquery()
        inner = (
            select(o.customer_id, o.order_id, products.label("products"), o.total)
            .where(o.customer_id > 0, *self._placed(start, end))
            .subquery()
        )
        return (
            select(func.concat(c.firstname, " ", c.lastname), c.email, func.count(), func.sum(inner.c.products),
                   func.sum(inner.c.total))
            .join(c, c.customer_id == inner.c.customer_id)
            .group_by(inner.c.customer_id, c.firstname, c.lastname, c.email)
            .order_by(func.sum(inner.c.total).desc())
        )  # fmt: skip

    def _customer_transactions(self, start, end, grouping):
        t, c = customer_transaction.c, DdCustomer
        return (
            select(func.concat(c.firstname, " ", c.lastname), c.email, func.sum(t.amount))
            .join(c, c.customer_id == t.customer_id)
            .where(t.date_added >= start, t.date_added < end)
            .group_by(t.customer_id, c.firstname, c.lastname, c.email)
            .order_by(func.sum(t.amount).desc())
        )

    def _customer_activity(self, start, end, grouping):
        a, c = customer_activity.c, DdCustomer
        name = func.coalesce(func.concat(c.firstname, " ", c.lastname), literal("гість"))
        return (
            select(func.date_format(a.date_added, "%Y-%m-%d %H:%i"), name, a.key, a.ip)
            .outerjoin(DdCustomer, c.customer_id == a.customer_id)
            .where(a.date_added >= start, a.date_added < end)
            .order_by(a.date_added.desc())
        )

    def _customer_searches(self, start, end, grouping):
        s = customer_search.c
        return (
            select(s.keyword, func.count(), func.max(s.products), func.date_format(func.max(s.date_added), "%Y-%m-%d"))
            .where(s.date_added >= start, s.date_added < end)
            .group_by(s.keyword)
            .order_by(func.count().desc())
        )

    def _statistics(self, start, end, grouping):
        s = statistics.c
        return select(s.code, s.value).order_by(s.code)
