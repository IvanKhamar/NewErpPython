"""Birthdays, coupons, a customer's orders and contact sources, straight from the OpenCart tables."""

from collections import defaultdict
from datetime import datetime, time

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.marketing.campaigns import BirthdayOrder, ContactExportRequest, CouponDraft, CustomerOrderDto
from newerp.domain.marketing import ContactField, ContactSource, OrderContact
from newerp.domain.sms import BIRTHDAY_MARKER
from newerp.infrastructure.models.marketing import DdCoupon, DdQuestion, DdSubscribe
from newerp.infrastructure.models.orders import DdOrder, DdOrderProduct, DdOrderStatus
from newerp.infrastructure.sms import sms_log


class SqlMarketingStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def birthday_orders(self) -> list[BirthdayOrder]:
        o = DdOrder
        rows = await self._session.execute(
            select(
                o.telephone, o.firstname, o.lastname, o.email, o.shipping_city, o.shipping_method, o.instagram,
                o.birthday_day, o.birthday_month, o.date_added,
            ).where(o.birthday_day > 0, o.birthday_month > 0)
        )  # fmt: skip
        return [BirthdayOrder(*row) for row in rows]

    async def birthday_sms_phones(self, since: datetime) -> list[str]:
        rows = await self._session.scalars(
            select(sms_log.c.phone).where(sms_log.c.pre_order_id == BIRTHDAY_MARKER, sms_log.c.date >= since)
        )
        return list(rows)

    async def coupon_exists(self, code: str) -> bool:
        return bool(await self._session.scalar(select(exists().where(DdCoupon.code == code))))

    async def add_coupon(self, coupon: CouponDraft) -> int:
        # Like the old site's createCoupon: a percent, no free shipping or minimal sum, one use.
        row = DdCoupon(
            code=coupon.code, name=coupon.name[:128], type="P", discount=coupon.discount_percent, logged=False,
            shipping=False, total=0, date_start=coupon.start, date_end=coupon.end, uses_total=1, uses_customer="0",
            status=True, date_added=coupon.created_at,
        )  # fmt: skip
        self._session.add(row)
        await self._session.commit()
        return row.coupon_id

    async def order_phones(self) -> list[tuple[int, str]]:
        rows = await self._session.execute(
            select(DdOrder.order_id, DdOrder.telephone).where(DdOrder.order_status_id > 0, DdOrder.telephone != "")
        )
        return [(order_id, phone) for order_id, phone in rows]

    async def orders(self, order_ids: list[int]) -> list[CustomerOrderDto]:
        o, s = DdOrder, DdOrderStatus
        orders = await self._session.execute(
            select(o.order_id, o.date_added, o.total, o.payment_method, func.coalesce(s.name, ""))
            .outerjoin(s, (s.order_status_id == o.order_status_id) & (s.language_id == self._language_id))
            .where(o.order_id.in_(order_ids))
            .order_by(o.date_added.desc())
        )
        lines: dict[int, list[str]] = defaultdict(list)
        for order_id, name, quantity in await self._session.execute(
            select(DdOrderProduct.order_id, DdOrderProduct.name, DdOrderProduct.quantity)
            .where(DdOrderProduct.order_id.in_(order_ids))
            .order_by(DdOrderProduct.order_product_id)
        ):
            lines[order_id].append(f"{name} x{quantity}")
        return [
            CustomerOrderDto(
                order_id=order_id,
                date_added=added,
                total=total,
                payment_method=method,
                status=status,
                products=", ".join(lines[order_id]),
            )  # fmt: skip
            for order_id, added, total, method, status in orders
        ]

    async def contact_values(self, request: ContactExportRequest) -> list[str]:
        phone = request.field == ContactField.PHONE
        values: list[str] = []
        if ContactSource.ORDERS in request.sources:
            column = DdOrder.telephone if phone else DdOrder.email
            values += await self._session.scalars(
                select(column).where(
                    DdOrder.order_status_id > 0, column != "", *_in_period(DdOrder.date_added, request)
                )
            )
        # As on the old site: questions have only a phone, subscriptions only an e-mail.
        if phone and ContactSource.QUESTIONS in request.sources:
            q = DdQuestion
            values += await self._session.scalars(
                select(q.telephone).where(q.telephone != "", *_in_period(q.date_added, request))
            )
        if not phone and ContactSource.SUBSCRIBERS in request.sources:
            s = DdSubscribe
            values += await self._session.scalars(
                select(s.email).where(s.email != "", *_in_period(s.date_added, request))
            )
        return values

    async def order_contacts(self) -> list[OrderContact]:
        o = DdOrder
        rows = await self._session.execute(
            select(o.telephone, o.email, o.firstname, o.lastname, o.shipping_city, o.date_added).where(
                o.order_status_id > 0
            )
        )
        return [OrderContact(*row) for row in rows]


def _in_period(column, request: ContactExportRequest) -> list:
    """from and to inclusive; a missing end is not limited."""
    conditions = []
    if request.from_ is not None:
        conditions.append(column >= datetime.combine(request.from_, time.min))
    if request.to is not None:
        conditions.append(column <= datetime.combine(request.to, time.max))
    return conditions
