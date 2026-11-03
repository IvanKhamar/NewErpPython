"""What the shipping checks read: the order, its latest waybill, the SMS log and the status log."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.orders.checks import OrderForCheck
from newerp.domain.orders import waybill_digits
from newerp.infrastructure.delivery.mapping import shipment_documents
from newerp.infrastructure.models.orders import DdOrder, DdOrderStatusLog
from newerp.infrastructure.sms import sms_log

NO_DATE_BELOW = datetime(2000, 1, 1)  # zero or empty waybill dates are left out, as the old query did


def _customer():
    return func.concat(DdOrder.firstname, " ", DdOrder.lastname)


class SqlShippingCheckStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def order_for_check(self, order_id: int) -> OrderForCheck | None:
        o = DdOrder
        row = (
            await self._session.execute(
                select(_customer(), o.telephone, o.shipping_code, o.shipping_method, o.date_added).where(
                    o.order_id == order_id
                )
            )
        ).first()
        return None if row is None else OrderForCheck(row[0].strip(), row[1], row[2], row[3], row[4])

    async def last_waybill(self, order_id: int) -> str:
        d = shipment_documents.c
        number = await self._session.scalar(
            select(d.number).where(d.order_id == order_id, d.number != "").order_by(d.id.desc()).limit(1)
        )
        return waybill_digits(number or "")

    async def sms_phones_with(self, waybill: str, since: datetime) -> list[str]:
        # The waybill is digits only, so it needs no escaping inside LIKE; it is still a bound parameter.
        s = sms_log.c
        rows = await self._session.scalars(
            select(s.phone).where(s.date >= since, func.replace(s.text, " ", "").contains(waybill_digits(waybill)))
        )
        return list(rows)

    async def stale_waybills(
        self, status_ids: tuple[int, ...], shipping_codes: tuple[str, ...], created_before: datetime
    ) -> list[tuple[int, str, datetime]]:
        d = shipment_documents.c
        created = func.max(d.date_created)
        rows = await self._session.execute(
            select(DdOrder.order_id, _customer(), created)
            .join(shipment_documents, d.order_id == DdOrder.order_id)
            .where(
                d.number != "",
                d.date_created > NO_DATE_BELOW,
                DdOrder.order_status_id.in_(status_ids),
                DdOrder.shipping_code.in_(shipping_codes),
            )
            .group_by(DdOrder.order_id, DdOrder.firstname, DdOrder.lastname)
            .having(created <= created_before)
            .order_by(DdOrder.order_id)
        )
        return [(row[0], row[1].strip(), row[2]) for row in rows]

    async def orders_moved_into(
        self, status_id: int, since: datetime, shipping_codes: tuple[str, ...] | None
    ) -> list[int]:
        log = DdOrderStatusLog
        query = (
            select(DdOrder.order_id)
            .where(
                DdOrder.order_status_id == status_id,
                select(log.order_status_log_id)
                .where(log.order_id == DdOrder.order_id, log.new_status_id == status_id, log.date_added >= since)
                .exists(),
            )
            .order_by(DdOrder.order_id)
        )
        if shipping_codes is not None:
            query = query.where(DdOrder.shipping_code.in_(shipping_codes))
        return list(await self._session.scalars(query))
