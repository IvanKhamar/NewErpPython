"""Mass price changes and the CSV import over dd_product, dd_product_description and dd_product_special."""

from decimal import Decimal

from sqlalchemy import delete, exists, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.catalog.prices import ImportedProduct, PricedProduct
from newerp.application.common import SystemClock
from newerp.infrastructure.catalog.product_edit import NO_END, NO_START
from newerp.infrastructure.models.catalog import DdProduct, DdProductDescription, DdProductSpecial, DdProductToCategory

SALE_GROUP_ID = 1  # the retail customer group, the one the storefront shows sales for


class SqlPriceStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def products(self, category_ids: list[int], manufacturer_ids: list[int]) -> list[PricedProduct]:
        p, d, s = DdProduct, DdProductDescription, DdProductSpecial
        percent = (
            select(func.max(s.percent))
            .where(s.product_id == p.product_id, s.customer_group_id == SALE_GROUP_ID)
            .scalar_subquery()
        )
        has_sale = exists().where(s.product_id == p.product_id, s.customer_group_id == SALE_GROUP_ID)
        query = select(p.product_id, func.coalesce(d.name, ""), p.model, p.price, percent, has_sale).outerjoin(
            d, (d.product_id == p.product_id) & (d.language_id == self._language_id)
        )
        if category_ids:
            query = query.where(
                exists().where(
                    DdProductToCategory.product_id == p.product_id, DdProductToCategory.category_id.in_(category_ids)
                )  # fmt: skip
            )
        if manufacturer_ids:
            query = query.where(p.manufacturer_id.in_(manufacturer_ids))
        rows = await self._session.execute(query.order_by(p.product_id))
        return [
            PricedProduct(product_id=r[0], name=r[1], model=r[2], price=r[3], sale_percent=r[4] if r[5] else None)
            for r in rows
        ]

    async def set_prices(self, prices: dict[int, Decimal]) -> None:
        for product_id, price in prices.items():
            await self._session.execute(
                update(DdProduct)
                .where(DdProduct.product_id == product_id)
                .values(price=price, date_modified=SystemClock().now().replace(microsecond=0))
            )
        await self._session.commit()

    async def set_sale_percents(self, product_ids: list[int], percent: Decimal) -> None:
        s = DdProductSpecial
        with_sale = set(
            await self._session.scalars(
                select(s.product_id).where(s.product_id.in_(product_ids), s.customer_group_id == SALE_GROUP_ID)
            )
        )
        if with_sale:
            # A percent sale has price 0: the old site counts it from the regular price.
            await self._session.execute(
                update(s)
                .where(s.product_id.in_(with_sale), s.customer_group_id == SALE_GROUP_ID)
                .values(percent=percent, price=0)
            )
        new = [i for i in product_ids if i not in with_sale]
        if new:
            await self._session.execute(
                insert(s),
                [
                    {
                        "product_id": i,
                        "customer_group_id": SALE_GROUP_ID,
                        "priority": 1,
                        "price": 0,
                        "percent": percent,
                        "date_start": NO_START.isoformat(),
                        "date_end": NO_END.isoformat(),
                    }  # fmt: skip
                    for i in new
                ],
            )
        await self._session.commit()

    async def remove_sales(self, product_ids: list[int]) -> None:
        s = DdProductSpecial
        await self._session.execute(
            delete(s).where(s.product_id.in_(product_ids), s.customer_group_id == SALE_GROUP_ID)
        )
        await self._session.commit()


class SqlImportStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def find(self, product_ids: list[int], models: list[str]) -> list[ImportedProduct]:
        if not product_ids and not models:
            return []
        p, d = DdProduct, DdProductDescription
        rows = await self._session.execute(
            select(
                p.product_id, p.model, p.price, p.weight, p.length, p.width, p.height, p.sort_order, p.status, p.sku,
                func.coalesce(d.name, ""),
            )
            .outerjoin(d, (d.product_id == p.product_id) & (d.language_id == self._language_id))
            .where(or_(p.product_id.in_(product_ids), p.model.in_(models)))
        )  # fmt: skip
        names = ("price", "weight", "length", "width", "height", "sort_order", "status", "sku", "name")
        return [
            ImportedProduct(
                product_id=r[0], model=r[1], values=dict(zip(names, (*r[2:8], int(r[8]), *r[9:]), strict=True))
            )
            for r in rows
        ]

    async def update(self, product_id: int, values: dict[str, object]) -> None:
        # date_modified changes too, so a product form opened before the import refuses to overwrite it (409).
        product = {column: value for column, value in values.items() if column != "name"}
        await self._session.execute(
            update(DdProduct)
            .where(DdProduct.product_id == product_id)
            .values({**product, "date_modified": SystemClock().now().replace(microsecond=0)})
        )
        if "name" in values:
            d = DdProductDescription
            await self._session.execute(
                update(d)
                .where(d.product_id == product_id, d.language_id == self._language_id)
                .values(name=values["name"])
            )
        await self._session.commit()
