"""Pre-order limits on dd_product_option_value (pre_order_quantity, date_available)."""

from datetime import date

from sqlalchemy import String, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.catalog.pre_orders import PreOrderFilter, PreOrderRow
from newerp.domain.catalog import availability, settle
from newerp.infrastructure.models.catalog import (
    DdOptionValueDescription,
    DdProduct,
    DdProductDescription,
    DdProductOptionValue,
)


class SqlPreOrderStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def list_rows(self, pre_order_filter: PreOrderFilter, limit: int) -> list[PreOrderRow]:
        v, d, vd = DdProductOptionValue, DdProductDescription, DdOptionValueDescription
        query = (
            select(
                v.product_option_value_id,
                v.product_id,
                d.name,
                DdProduct.model,
                func.coalesce(vd.name, ""),
                v.quantity,
                v.pre_order_quantity,
                v.subtract,
                # As text: the zero date 0000-00-00 is not a valid date.
                cast(v.date_available, String),
            )  # fmt: skip
            .join(DdProduct, DdProduct.product_id == v.product_id)
            .join(d, (d.product_id == DdProduct.product_id) & (d.language_id == self._language_id))
            .outerjoin(vd, (vd.option_value_id == v.option_value_id) & (vd.language_id == self._language_id))
            .order_by(d.name, v.product_id, v.product_option_value_id)
            .limit(limit)
        )
        if search := pre_order_filter.search:
            query = query.where(
                d.name.contains(search, autoescape=True) | DdProduct.model.contains(search, autoescape=True)
            )
        if pre_order_filter.only_with_quota:
            query = query.where(v.pre_order_quantity > 0)
        return [
            PreOrderRow(
                product_option_value_id=r[0],
                product_id=r[1],
                product_name=r[2],
                model=r[3],
                size=r[4],
                quantity=r[5],
                quota=r[6],
                date_available=_parse_date(r[8]),
                availability=availability(r[5], r[6], r[7]),
            )  # fmt: skip
            for r in await self._session.execute(query)
        ]

    async def update(self, product_option_value_id: int, quota: int, date_available: date | None) -> bool:
        result = await self._session.execute(
            update(DdProductOptionValue)
            .where(DdProductOptionValue.product_option_value_id == product_option_value_id)
            .values(pre_order_quantity=quota, date_available=date_available)
        )
        await self._session.commit()
        return result.rowcount > 0

    async def settle(self, product_ids: list[int] | None) -> int:
        v = DdProductOptionValue
        query = select(v.product_option_value_id, v.quantity, v.pre_order_quantity).where(
            v.quantity < 0, v.pre_order_quantity > 0
        )
        if product_ids is not None:
            query = query.where(v.product_id.in_(product_ids))
        rows = (await self._session.execute(query)).all()
        # MyISAM has no transactions, so each size is its own UPDATE guarded by the old values: if the storefront sold
        # more in the meantime, the row stays as it is and is settled next time.
        changed = 0
        for row_id, quantity, quota in rows:
            new_quantity, new_quota = settle(quantity, quota)
            result = await self._session.execute(
                update(v)
                .where(v.product_option_value_id == row_id, v.quantity == quantity, v.pre_order_quantity == quota)
                .values(quantity=new_quantity, pre_order_quantity=new_quota)
            )
            changed += result.rowcount
        await self._session.commit()
        return changed


def _parse_date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:  # 0000-00-00
        return None
