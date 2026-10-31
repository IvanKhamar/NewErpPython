"""Shop products straight from the OpenCart tables, like the Java JdbcProductStore: the same order and filters."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, exists, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.catalog.products import ProductCardDto, ProductFilter, ProductListItem, ProductSize
from newerp.application.common import ConflictError, LookupItem, PagedResult, ukrainian_key
from newerp.infrastructure.models.catalog import (
    DdCategory,
    DdCategoryDescription,
    DdOptionValueDescription,
    DdProduct,
    DdProductDescription,
    DdProductLabel,
    DdProductOptionValue,
    DdProductToCategory,
    DdProductToLabel,
)


class SqlProductStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def list_page(self, product_filter: ProductFilter) -> PagedResult[ProductListItem]:
        name = func.coalesce(DdProductDescription.name, "")
        query = select(DdProduct.product_id).outerjoin(
            DdProductDescription,
            (DdProductDescription.product_id == DdProduct.product_id)
            & (DdProductDescription.language_id == self._language_id),
        )
        f = product_filter
        if f.category_id is not None:
            query = query.where(
                exists().where(
                    DdProductToCategory.product_id == DdProduct.product_id,
                    DdProductToCategory.category_id == f.category_id,
                )
            )
        if f.label_id is not None:
            query = query.where(
                exists().where(
                    DdProductToLabel.product_id == DdProduct.product_id, DdProductToLabel.label_id == f.label_id
                )
            )
        if f.enabled is not None:
            query = query.where(DdProduct.status == f.enabled)
        if f.search:
            # autoescape: "50%" finds exactly "50%", not everything.
            in_model = DdProduct.model.contains(f.search, autoescape=True)
            if f.search.isdigit():
                query = query.where((DdProduct.product_id == int(f.search)) | in_model)
            else:
                query = query.where(name.contains(f.search, autoescape=True) | in_model)

        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await self._session.execute(
            query.with_only_columns(
                DdProduct.product_id,
                name,
                DdProduct.model,
                DdProduct.price,
                DdProduct.status,
                DdProduct.quantity,
                DdProduct.image,
            )  # fmt: skip
            .order_by(name, DdProduct.product_id)
            .offset((f.page - 1) * f.page_size)
            .limit(f.page_size)
        )
        items = [
            ProductListItem(id=r[0], name=r[1], model=r[2], price=r[3], enabled=r[4], quantity=r[5], image=r[6])
            for r in rows
        ]
        return PagedResult[ProductListItem](items=items, page=f.page, page_size=f.page_size, total_count=total or 0)

    async def get(self, product_id: int) -> ProductCardDto | None:
        product = (
            await self._session.execute(
                select(
                    DdProduct.product_id,
                    func.coalesce(DdProductDescription.name, ""),
                    DdProduct.model,
                    DdProduct.price,
                    DdProduct.status,
                    DdProduct.quantity,
                    DdProduct.image,
                    DdProduct.date_modified,
                )
                .outerjoin(
                    DdProductDescription,
                    (DdProductDescription.product_id == DdProduct.product_id)
                    & (DdProductDescription.language_id == self._language_id),
                )
                .where(DdProduct.product_id == product_id)
            )
        ).first()
        if product is None:
            return None

        # A size without a name in this language is still shown, by its number.
        v, n = DdProductOptionValue, DdOptionValueDescription
        sizes = await self._session.execute(
            select(v.option_value_id, n.name, v.quantity)
            .outerjoin(n, (n.option_value_id == v.option_value_id) & (n.language_id == self._language_id))
            .where(v.product_id == product_id)
            .order_by(v.product_option_value_id)
        )
        labels = await self._session.execute(
            select(DdProductLabel.label_id, DdProductLabel.name)
            .join(DdProductToLabel, DdProductToLabel.label_id == DdProductLabel.label_id)
            .where(DdProductToLabel.product_id == product_id)
            .order_by(DdProductLabel.sort_order, DdProductLabel.name)
        )
        categories = await self._session.execute(
            select(DdProductToCategory.category_id, DdCategoryDescription.name)
            .outerjoin(
                DdCategoryDescription,
                (DdCategoryDescription.category_id == DdProductToCategory.category_id)
                & (DdCategoryDescription.language_id == self._language_id),
            )
            .where(DdProductToCategory.product_id == product_id)
        )
        category_items = [LookupItem(id=c, name=name or f"#{c}") for c, name in categories]
        return ProductCardDto(
            id=product[0],
            name=product[1],
            model=product[2],
            price=product[3],
            enabled=product[4],
            quantity=product[5],
            image=product[6],
            date_modified=product[7],
            sizes=[ProductSize(option_value_id=i, name=name or f"#{i}", quantity=q) for i, name, q in sizes],
            labels=[LookupItem(id=i, name=name) for i, name in labels],
            categories=sorted(category_items, key=lambda c: ukrainian_key(c.name)),
        )

    async def update(
        self, product_id: int, expected: datetime, price: Decimal | None, enabled: bool | None, now: datetime
    ) -> None:
        # dd_product is MyISAM: no transactions, no row locks. date_modified works as the version: the UPDATE touches
        # only the row the user saw, and a save in OpenCart sets date_modified too, so a change from there shows.
        values: dict[str, object] = {"date_modified": now}
        if price is not None:
            values["price"] = price
        if enabled is not None:
            values["status"] = enabled
        result = await self._session.execute(
            update(DdProduct)
            .where(DdProduct.product_id == product_id, DdProduct.date_modified == expected)
            .values(values)
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        if result.rowcount == 0:
            raise ConflictError(
                f"Товар №{product_id} змінили в OpenCart або в іншому вікні після того, як ви його відкрили. "
                "Оновіть сторінку й повторіть зміни."
            )

    async def replace_labels(self, product_id: int, label_ids: list[int]) -> None:
        await self._session.execute(delete(DdProductToLabel).where(DdProductToLabel.product_id == product_id))
        if label_ids:
            await self._session.execute(
                insert(DdProductToLabel), [{"product_id": product_id, "label_id": i} for i in label_ids]
            )
        await self._session.commit()

    async def replace_categories(self, product_id: int, category_ids: list[int]) -> None:
        await self._session.execute(delete(DdProductToCategory).where(DdProductToCategory.product_id == product_id))
        await self._session.execute(
            insert(DdProductToCategory), [{"product_id": product_id, "category_id": i} for i in category_ids]
        )
        await self._session.commit()

    async def labels(self) -> list[LookupItem]:
        rows = await self._session.execute(
            select(DdProductLabel.label_id, DdProductLabel.name).order_by(
                DdProductLabel.sort_order, DdProductLabel.name
            )
        )
        return [LookupItem(id=i, name=name) for i, name in rows]

    async def categories(self) -> list[LookupItem]:
        rows = await self._session.execute(
            select(DdCategory.category_id, DdCategoryDescription.name)
            .join(
                DdCategoryDescription,
                (DdCategoryDescription.category_id == DdCategory.category_id)
                & (DdCategoryDescription.language_id == self._language_id),
            )
            .where(DdCategory.status.is_(True))
            .order_by(DdCategoryDescription.name)
        )
        return [LookupItem(id=i, name=name) for i, name in rows]
