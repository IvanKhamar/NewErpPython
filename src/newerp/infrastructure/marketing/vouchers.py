"""Gift vouchers in dd_voucher and their spendings in dd_voucher_history and the order totals."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.common import LookupItem, ukrainian_key
from newerp.application.marketing.vouchers import OrderSums, SpentInOrder, VoucherHistoryRow, VoucherRow
from newerp.domain.marketing import GiftVoucher
from newerp.infrastructure.models.marketing import (
    DdVoucher,
    DdVoucherHistory,
    DdVoucherTheme,
    DdVoucherThemeDescription,
)
from newerp.infrastructure.models.orders import DdOrder, DdOrderTotal

MAX_ROWS = 500


class SqlVoucherStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def list_rows(self, search: str) -> list[VoucherRow]:
        query = self._rows().order_by(DdVoucher.voucher_id.desc()).limit(MAX_ROWS)
        if search:
            v = DdVoucher
            query = query.where(
                v.code.contains(search, autoescape=True)
                | v.to_name.contains(search, autoescape=True)
                | v.from_name.contains(search, autoescape=True)
            )
        return await self._fetch(query)

    async def find(self, voucher_id: int) -> VoucherRow | None:
        rows = await self._fetch(self._rows().where(DdVoucher.voucher_id == voucher_id))
        return rows[0] if rows else None

    async def find_by_code(self, code: str) -> VoucherRow | None:
        rows = await self._fetch(
            self._rows().where(DdVoucher.code == code).order_by(DdVoucher.voucher_id.desc()).limit(1)
        )
        return rows[0] if rows else None

    async def code_taken(self, code: str, except_id: int | None) -> bool:
        query = select(DdVoucher.voucher_id).where(DdVoucher.code == code, DdVoucher.voucher_id != (except_id or 0))
        return await self._session.scalar(query.limit(1)) is not None

    async def add(self, voucher: GiftVoucher, now: datetime) -> int:
        row = DdVoucher(order_id=0, date_added=now, **_columns(voucher))
        self._session.add(row)
        await self._session.commit()
        return row.voucher_id

    async def update(self, voucher_id: int, voucher: GiftVoucher) -> None:
        await self._session.execute(
            update(DdVoucher).where(DdVoucher.voucher_id == voucher_id).values(**_columns(voucher))
        )
        await self._session.commit()

    async def delete(self, voucher_id: int) -> None:
        await self._session.execute(delete(DdVoucher).where(DdVoucher.voucher_id == voucher_id))
        await self._session.commit()

    async def history(self, voucher_id: int) -> list[VoucherHistoryRow]:
        h, o = DdVoucherHistory, DdOrder
        customer = func.coalesce(func.concat(o.firstname, " ", o.lastname), "")
        rows = await self._session.execute(
            select(h.order_id, customer, h.amount, h.date_added)
            .outerjoin(o, o.order_id == h.order_id)
            .where(h.voucher_id == voucher_id)
            .order_by(h.voucher_history_id)
        )
        return [
            VoucherHistoryRow(order_id=order_id, customer=name, amount=amount, date=added)
            for order_id, name, amount, added in rows
        ]

    async def themes(self) -> list[LookupItem]:
        d = DdVoucherThemeDescription
        rows = await self._session.execute(
            select(DdVoucherTheme.voucher_theme_id, d.name).outerjoin(
                d, (d.voucher_theme_id == DdVoucherTheme.voucher_theme_id) & (d.language_id == self._language_id)
            )
        )
        themes = [LookupItem(id=i, name=name or f"#{i}") for i, name in rows]
        return sorted(themes, key=lambda t: ukrainian_key(t.name))

    async def order_sums(self, order_id: int) -> OrderSums | None:
        row = (
            await self._session.execute(
                select(DdOrder.total, DdOrder.prepayment).where(
                    DdOrder.order_id == order_id, DdOrder.order_status_id > 0
                )
            )
        ).first()
        return OrderSums(*row) if row else None

    async def spent_in_order(self, order_id: int) -> SpentInOrder | None:
        h = DdVoucherHistory
        spent = -func.sum(h.amount)
        row = (
            await self._session.execute(
                select(h.voucher_id, spent)
                .where(h.order_id == order_id)
                .group_by(h.voucher_id)
                .having(spent != 0)
                .order_by(h.voucher_id)
                .limit(1)
            )
        ).first()
        return SpentInOrder(*row) if row else None

    async def redeem(self, order_id: int, voucher_id: int, code: str, amount: Decimal, now: datetime) -> None:
        # OpenCart tables are MyISAM, without transactions: the same rows as at the shop checkout, one after another.
        # The voucher row stands right before the total (sort_order 98 and 99), as OpenCart sorts them.
        await self._session.execute(
            insert(DdVoucherHistory).values(voucher_id=voucher_id, order_id=order_id, amount=-amount, date_added=now)
        )
        await self._session.execute(
            insert(DdOrderTotal).values(
                order_id=order_id,
                code="voucher",
                title=f"Подарунковий сертифікат ({code})",
                value=-amount,
                sort_order=98,
            )  # fmt: skip
        )
        await self._change_total(order_id, -amount)

    async def release(self, order_id: int, voucher_id: int, amount: Decimal) -> None:
        await self._session.execute(
            delete(DdVoucherHistory).where(
                DdVoucherHistory.order_id == order_id, DdVoucherHistory.voucher_id == voucher_id
            )
        )
        await self._session.execute(
            delete(DdOrderTotal).where(DdOrderTotal.order_id == order_id, DdOrderTotal.code == "voucher")
        )
        await self._change_total(order_id, amount)

    async def _change_total(self, order_id: int, by: Decimal) -> None:
        await self._session.execute(
            update(DdOrderTotal)
            .where(DdOrderTotal.order_id == order_id, DdOrderTotal.code == "total")
            .values(value=DdOrderTotal.value + by, sort_order=99)
        )
        await self._session.execute(
            update(DdOrder).where(DdOrder.order_id == order_id).values(total=DdOrder.total + by)
        )
        await self._session.commit()

    def _rows(self) -> Select:
        """A voucher with its theme name, the spent sum and the status of the order it was bought in, in one row."""
        v, d, h, o = DdVoucher, DdVoucherThemeDescription, DdVoucherHistory, DdOrder
        theme_name = (
            select(d.name)
            .where(d.voucher_theme_id == v.voucher_theme_id, d.language_id == self._language_id)
            .limit(1)
            .scalar_subquery()
        )
        spent = select(func.coalesce(func.sum(h.amount), 0)).where(h.voucher_id == v.voucher_id).scalar_subquery()
        order_status = (
            select(o.order_status_id).where(o.order_id == v.order_id, v.order_id > 0).limit(1).scalar_subquery()
        )
        return select(
            v.voucher_id, v.order_id, v.code, v.from_name, v.from_email, v.to_name, v.to_email, v.voucher_theme_id,
            func.coalesce(theme_name, ""), v.message, v.amount, v.status, v.date_added, spent, order_status,
        )  # fmt: skip

    async def _fetch(self, query: Select) -> list[VoucherRow]:
        return [VoucherRow(*row) for row in await self._session.execute(query)]


def _columns(v: GiftVoucher) -> dict:
    return {
        "code": v.code, "from_name": v.from_name, "from_email": v.from_email, "to_name": v.to_name,
        "to_email": v.to_email, "voucher_theme_id": v.theme_id, "message": v.message, "amount": v.amount,
        "status": v.active,
    }  # fmt: skip
