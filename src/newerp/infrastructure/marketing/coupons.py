"""Coupons in the OpenCart tables: dd_coupon with its product and category lists and the old site's exclusions."""

from datetime import date, datetime

from sqlalchemy import case, delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.common import LookupItem
from newerp.application.marketing.coupons import CouponCardDto, CouponHistoryRow, CouponListItem
from newerp.domain.marketing import Coupon
from newerp.infrastructure.models.catalog import DdCategoryDescription, DdProduct, DdProductDescription
from newerp.infrastructure.models.marketing import (
    DdCoupon,
    DdCouponCategory,
    DdCouponCategoryExclude,
    DdCouponHistory,
    DdCouponProduct,
    DdCouponProductExclude,
)

# A stored 0000-00-00 ("no limit") is below any real date; strict MySQL refuses that literal, and Python has no such
# date, so the queries turn it into NULL.
FIRST_DATE = date(1000, 1, 1)


def _date_or_none(column):
    return case((column < FIRST_DATE, None), else_=column)


class SqlCouponStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def list_coupons(self, search: str, limit: int) -> list[CouponListItem]:
        c = DdCoupon
        used = select(func.count()).where(DdCouponHistory.coupon_id == c.coupon_id).correlate(c).scalar_subquery()
        query = select(
            c.coupon_id, c.name, c.code, c.type, c.discount, c.total, _date_or_none(c.date_start),
            _date_or_none(c.date_end), c.uses_total, c.status, used,
        )  # fmt: skip
        if search:
            query = query.where(or_(c.code.contains(search, autoescape=True), c.name.contains(search, autoescape=True)))
        rows = await self._session.execute(query.order_by(c.coupon_id.desc()).limit(limit))
        return [
            CouponListItem(
                id=r[0],
                name=r[1],
                code=r[2],
                type=r[3],
                discount=r[4],
                total=r[5],
                date_start=r[6],
                date_end=r[7],
                uses_total=r[8],
                active=r[9],
                used=r[10],
            )  # fmt: skip
            for r in rows
        ]

    async def get(self, coupon_id: int) -> CouponCardDto | None:
        c = DdCoupon
        row = (
            await self._session.execute(
                select(
                    c.coupon_id,
                    c.name,
                    c.code,
                    c.type,
                    c.discount,
                    c.total,
                    c.logged,
                    c.shipping,
                    _date_or_none(c.date_start),
                    _date_or_none(c.date_end),
                    c.uses_total,
                    c.uses_customer,
                    c.status,
                    c.date_added,
                ).where(c.coupon_id == coupon_id)  # fmt: skip
            )
        ).first()
        if row is None:
            return None
        history = await self._session.execute(
            select(
                DdCouponHistory.order_id,
                DdCouponHistory.customer_id,
                DdCouponHistory.amount,
                DdCouponHistory.date_added,
            )  # fmt: skip
            .where(DdCouponHistory.coupon_id == coupon_id)
            .order_by(DdCouponHistory.date_added.desc())
        )
        uses_customer = int(row[11]) if str(row[11]).strip().isdigit() else 0  # varchar(11) in OpenCart
        return CouponCardDto(
            id=row[0], name=row[1], code=row[2], type=row[3], discount=row[4], total=row[5], logged=row[6],
            shipping=row[7], date_start=row[8], date_end=row[9], uses_total=row[10], uses_customer=uses_customer,
            active=row[12], date_added=row[13],
            products=await self._products(DdCouponProduct.product_id, DdCouponProduct.coupon_id, coupon_id),
            categories=await self._categories(DdCouponCategory.category_id, DdCouponCategory.coupon_id, coupon_id),
            excluded_products=await self._products(
                DdCouponProductExclude.product_id, DdCouponProductExclude.coupon_id, coupon_id
            ),
            excluded_categories=await self._categories(
                DdCouponCategoryExclude.category_id, DdCouponCategoryExclude.coupon_id, coupon_id
            ),
            history=[CouponHistoryRow(order_id=h[0], customer_id=h[1], amount=h[2], date_added=h[3]) for h in history],
        )  # fmt: skip

    async def _products(self, product_column, coupon_column, coupon_id: int) -> list[LookupItem]:
        d = DdProductDescription
        rows = await self._session.execute(
            select(product_column, func.coalesce(d.name, ""))
            .outerjoin(d, (d.product_id == product_column) & (d.language_id == self._language_id))
            .where(coupon_column == coupon_id)
            .order_by(product_column)
        )
        return [LookupItem(id=pid, name=name) for pid, name in rows]

    async def _categories(self, category_column, coupon_column, coupon_id: int) -> list[LookupItem]:
        d = DdCategoryDescription
        rows = await self._session.execute(
            select(category_column, func.coalesce(d.name, ""))
            .outerjoin(d, (d.category_id == category_column) & (d.language_id == self._language_id))
            .where(coupon_column == coupon_id)
            .order_by(category_column)
        )
        return [LookupItem(id=cid, name=name) for cid, name in rows]

    async def code_taken(self, code: str, except_id: int | None) -> bool:
        condition = DdCoupon.code == code
        if except_id is not None:
            condition = condition & (DdCoupon.coupon_id != except_id)
        return bool(await self._session.scalar(select(exists().where(condition))))

    async def missing_products(self, product_ids: list[int]) -> list[int]:
        if not product_ids:
            return []
        found = set(
            await self._session.scalars(select(DdProduct.product_id).where(DdProduct.product_id.in_(product_ids)))
        )
        return [pid for pid in dict.fromkeys(product_ids) if pid not in found]

    async def missing_categories(self, category_ids: list[int]) -> list[int]:
        if not category_ids:
            return []
        d = DdCategoryDescription
        found = set(await self._session.scalars(select(d.category_id).where(d.category_id.in_(category_ids))))
        return [cid for cid in dict.fromkeys(category_ids) if cid not in found]

    async def add(self, coupon: Coupon, now: datetime) -> int:
        row = DdCoupon(date_added=now, **_values(coupon))
        self._session.add(row)
        await self._session.flush()  # the new id, for the lists
        self._add_lists(row.coupon_id, coupon)
        await self._session.commit()
        return row.coupon_id

    async def update(self, coupon_id: int, coupon: Coupon) -> bool:
        # An UPDATE statement, not a loaded row: an old coupon may hold 0000-00-00, which no Python date can read.
        if not await self._session.scalar(select(exists().where(DdCoupon.coupon_id == coupon_id))):
            return False
        await self._session.execute(update(DdCoupon).where(DdCoupon.coupon_id == coupon_id).values(**_values(coupon)))
        await self._delete_lists(coupon_id)
        self._add_lists(coupon_id, coupon)
        await self._session.commit()
        return True

    async def used(self, coupon_id: int) -> int:
        count = await self._session.scalar(select(func.count()).where(DdCouponHistory.coupon_id == coupon_id))
        return int(count or 0)

    async def delete(self, coupon_id: int) -> None:
        await self._delete_lists(coupon_id)
        await self._session.execute(delete(DdCoupon).where(DdCoupon.coupon_id == coupon_id))
        await self._session.commit()

    def _add_lists(self, coupon_id: int, coupon: Coupon) -> None:
        self._session.add_all(
            [DdCouponProduct(coupon_id=coupon_id, product_id=pid) for pid in coupon.product_ids]
            + [DdCouponCategory(coupon_id=coupon_id, category_id=cid) for cid in coupon.category_ids]
            + [DdCouponProductExclude(coupon_id=coupon_id, product_id=pid) for pid in coupon.excluded_product_ids]
            + [DdCouponCategoryExclude(coupon_id=coupon_id, category_id=cid) for cid in coupon.excluded_category_ids]
        )

    async def _delete_lists(self, coupon_id: int) -> None:
        for table in (DdCouponProduct, DdCouponCategory, DdCouponProductExclude, DdCouponCategoryExclude):
            await self._session.execute(delete(table).where(table.coupon_id == coupon_id))


def _values(coupon: Coupon) -> dict:
    return {
        "name": coupon.name,
        "code": coupon.code,
        "type": coupon.type,
        "discount": coupon.discount,
        "total": coupon.total,
        "logged": coupon.logged,
        "shipping": coupon.shipping,
        "date_start": coupon.start,
        "date_end": coupon.end,
        "uses_total": coupon.uses_total,
        "uses_customer": str(coupon.uses_customer),  # varchar(11) in OpenCart
        "status": coupon.active,
    }
