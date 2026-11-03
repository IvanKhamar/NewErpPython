"""The product form over the OpenCart tables, as the old admin's addProduct / editProduct wrote them.

MyISAM has no transactions: the guarded UPDATE of dd_product goes first (it is the version check), then each linked
table is replaced. A crash in between leaves the product with some old links, and saving again repairs it."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, case, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.catalog.product_edit import (
    ProductEditDto,
    ProductFormDto,
    ProductFormLookups,
    ProductImageDto,
    ProductSizeEditDto,
    ProductSpecialDto,
    RemoveNewResult,
)
from newerp.application.common import ConflictError, LookupItem, ukrainian_key
from newerp.domain.catalog import NEW_LABEL, without_new_tag
from newerp.infrastructure.models.catalog import (
    DdCategory,
    DdCategoryDescription,
    DdFilter,
    DdFilterDescription,
    DdOptionDescription,
    DdOptionValueDescription,
    DdProduct,
    DdProductDescription,
    DdProductFilter,
    DdProductImage,
    DdProductLabel,
    DdProductOptionValue,
    DdProductSpecial,
    DdProductToCategory,
    DdProductToLabel,
    DdProductToStore,
)

SIZE_OPTION_NAME = "Розмір"  # the option whose values are sizes, as the partner feeds find it
THEME_FILTER_GROUP_ID = 7  # "За темою", where the NEW filter of a new print lives (the old admin's constant)
NO_START, NO_END = date(1000, 1, 1), date(9999, 12, 31)  # "no limit": MySQL in strict mode refuses '0000-00-00'
STORE_ID = 0

metadata = MetaData()
seo_url = Table(
    "dd_seo_url",
    metadata,
    Column("seo_url_id", Integer, primary_key=True),
    Column("store_id", Integer),
    Column("language_id", Integer),
    Column("query", String(255)),
    Column("keyword", String(255)),
)
product_option = Table(
    "dd_product_option",
    metadata,
    Column("product_option_id", Integer, primary_key=True),
    Column("product_id", Integer),
    Column("option_id", Integer),
    Column("value", Text),
    Column("required", Integer),
)
product_color = Table(
    "dd_product_color",
    metadata,
    Column("product_id", Integer, primary_key=True),
    Column("language_id", Integer, primary_key=True),
    Column("name", String(255)),
)


def _links(name: str) -> Table:
    return Table(name, metadata, Column("product_id", Integer, primary_key=True), Column("related_id", Integer))


related = _links("dd_product_related")
related_color = _links("dd_product_related_color")
related_variant = _links("dd_product_related_variant")
# The colour and variant links of the old site go both ways: a product is linked to its colours and they to it.
TWO_WAY = (related_color, related_variant)


def _no_limit(column, edge: date):
    """A sale date as the form shows it: the zero date and the "no limit" edges are empty."""
    return case((column < "1000-01-02", None), (column == edge, None), else_=column)


class SqlProductEditStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def get(self, product_id: int) -> ProductEditDto | None:
        p = await self._session.get(DdProduct, product_id)
        if p is None:
            return None
        d = await self._session.get(DdProductDescription, (product_id, self._language_id))
        color = await self._session.scalar(
            select(product_color.c.name).where(
                product_color.c.product_id == product_id, product_color.c.language_id == self._language_id
            )
        )
        s = DdProductSpecial
        specials = await self._session.execute(
            select(
                s.customer_group_id,
                s.priority,
                s.price,
                s.percent,
                _no_limit(s.date_start, NO_START),
                _no_limit(s.date_end, NO_END),
            )
            .where(s.product_id == product_id)
            .order_by(s.priority, s.product_special_id)  # fmt: skip
        )
        v, n = DdProductOptionValue, DdOptionValueDescription
        sizes = await self._session.execute(
            select(v.option_value_id, n.name, v.price, v.quantity)
            .outerjoin(n, (n.option_value_id == v.option_value_id) & (n.language_id == self._language_id))
            .where(v.product_id == product_id)
            .order_by(v.product_option_value_id)
        )
        images = await self._session.scalars(
            select(DdProductImage).where(DdProductImage.product_id == product_id).order_by(DdProductImage.sort_order)
        )
        keyword = await self._session.scalar(
            select(seo_url.c.keyword).where(
                seo_url.c.query == f"product_id={product_id}",
                seo_url.c.store_id == STORE_ID,
                seo_url.c.language_id == self._language_id,
            )  # fmt: skip
        )

        async def ids(column, owner) -> list[int]:
            return list(await self._session.scalars(select(column).where(owner == product_id)))

        text = {
            name: (getattr(d, name) or "") if d else ""
            for name in (
                "name description tag meta_title meta_description meta_keyword table_size variant notice model_info "
                "characteristics"
            ).split()
        }
        return ProductEditDto(
            id=p.product_id, date_modified=p.date_modified, model=p.model, sku=p.sku, price=p.price,
            minimum=p.minimum, subtract=p.subtract, stock_status_id=p.stock_status_id,
            manufacturer_id=p.manufacturer_id, weight=p.weight, length=p.length, width=p.width, height=p.height,
            sort_order=p.sort_order, enabled=p.status, image=p.image or "", color=color or "",
            seo_keyword=keyword or "",
            category_ids=await ids(DdProductToCategory.category_id, DdProductToCategory.product_id),
            label_ids=await ids(DdProductToLabel.label_id, DdProductToLabel.product_id),
            filter_ids=await ids(DdProductFilter.filter_id, DdProductFilter.product_id),
            related_ids=await ids(related.c.related_id, related.c.product_id),
            related_color_ids=await ids(related_color.c.related_id, related_color.c.product_id),
            related_variant_ids=await ids(related_variant.c.related_id, related_variant.c.product_id),
            images=[ProductImageDto(image=i.image or "", sort_order=i.sort_order) for i in images],
            specials=[
                ProductSpecialDto(customer_group_id=r[0], priority=r[1], price=r[2], percent=r[3], date_start=r[4],
                                  date_end=r[5])  # fmt: skip
                for r in specials
            ],
            sizes=[
                ProductSizeEditDto(option_value_id=i, name=name or f"#{i}", price=price, quantity=quantity)
                for i, name, price, quantity in sizes
            ],
            **text,
        )  # fmt: skip

    async def lookups(self) -> ProductFormLookups:
        categories = await self._session.execute(
            select(DdCategory.category_id, DdCategoryDescription.name)
            .join(
                DdCategoryDescription,
                (DdCategoryDescription.category_id == DdCategory.category_id)
                & (DdCategoryDescription.language_id == self._language_id),
            )
            .where(DdCategory.status.is_(True))
        )
        labels = await self._session.execute(
            select(DdProductLabel.label_id, DdProductLabel.name).order_by(DdProductLabel.sort_order)
        )
        filters = await self._session.execute(
            select(DdFilter.filter_id, DdFilterDescription.name, DdFilter.filter_group_id)
            .join(
                DdFilterDescription,
                (DdFilterDescription.filter_id == DdFilter.filter_id)
                & (DdFilterDescription.language_id == self._language_id),
            )
            .order_by(DdFilter.filter_group_id, DdFilter.sort_order)
        )
        sizes = await self._session.execute(
            select(DdOptionValueDescription.option_value_id, DdOptionValueDescription.name)
            .join(DdOptionDescription, DdOptionDescription.option_id == DdOptionValueDescription.option_id)
            .where(
                DdOptionDescription.name.contains(SIZE_OPTION_NAME),
                DdOptionDescription.language_id == self._language_id,
                DdOptionValueDescription.language_id == self._language_id,
            )
            .order_by(DdOptionValueDescription.option_value_id)
        )
        return ProductFormLookups(
            categories=sorted((LookupItem(id=i, name=n) for i, n in categories), key=lambda c: ukrainian_key(c.name)),
            labels=[LookupItem(id=i, name=n) for i, n in labels],
            filters=[LookupItem(id=i, name=f"{g}: {n}") for i, n, g in filters],
            sizes=[LookupItem(id=i, name=n) for i, n in sizes],
        )

    async def existing_products(self, product_ids: set[int]) -> set[int]:
        if not product_ids:
            return set()
        return set(
            await self._session.scalars(select(DdProduct.product_id).where(DdProduct.product_id.in_(product_ids)))
        )

    async def seo_keyword_owner(self, keyword: str) -> str | None:
        return await self._session.scalar(
            select(seo_url.c.query).where(seo_url.c.keyword == keyword, seo_url.c.store_id == STORE_ID).limit(1)
        )

    async def create(self, form: ProductFormDto, now: datetime) -> int:
        product = DdProduct(
            model=form.model, quantity=0, date_available=now.date(), date_added=now, date_modified=now,
            **self._product_values(form),
        )  # fmt: skip
        self._session.add(product)
        await self._session.flush()
        self._session.add(DdProductToStore(product_id=product.product_id, store_id=STORE_ID))
        await self._session.commit()
        await self._write_links(product.product_id, form, sizes_before=set())
        return product.product_id

    async def save(self, product_id: int, form: ProductFormDto, expected: datetime, now: datetime) -> None:
        result = await self._session.execute(
            update(DdProduct)
            .where(DdProduct.product_id == product_id, DdProduct.date_modified == expected)
            .values(model=form.model, date_modified=now, **self._product_values(form))
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        if result.rowcount == 0:
            raise ConflictError(
                f"Товар №{product_id} змінили в OpenCart або в іншому вікні після того, як ви його відкрили. "
                "Оновіть сторінку й повторіть зміни."
            )
        v = DdProductOptionValue
        before = await self._session.scalars(select(v.option_value_id).where(v.product_id == product_id))
        await self._write_links(product_id, form, sizes_before=set(before))

    async def remove_new(self, sku: str) -> RemoveNewResult:
        product_ids = list(await self._session.scalars(select(DdProduct.product_id).where(DdProduct.sku == sku)))
        if not product_ids:
            return RemoveNewResult(product_ids=[], label_removed=False, filter_removed=False)
        label_id = await self._session.scalar(select(DdProductLabel.label_id).where(DdProductLabel.name == NEW_LABEL))
        filter_id = await self._session.scalar(
            select(DdFilter.filter_id)
            .join(DdFilterDescription, DdFilterDescription.filter_id == DdFilter.filter_id)
            .where(
                DdFilter.filter_group_id == THEME_FILTER_GROUP_ID,
                DdFilterDescription.name == NEW_LABEL,
                DdFilterDescription.language_id == self._language_id,
            )
        )
        if label_id is not None:
            await self._session.execute(
                delete(DdProductToLabel).where(
                    DdProductToLabel.product_id.in_(product_ids), DdProductToLabel.label_id == label_id
                )
            )
        if filter_id is not None:
            await self._session.execute(
                delete(DdProductFilter).where(
                    DdProductFilter.product_id.in_(product_ids), DdProductFilter.filter_id == filter_id
                )
            )
        d = DdProductDescription
        tagged = await self._session.execute(
            select(d.product_id, d.language_id, d.tag).where(d.product_id.in_(product_ids), d.tag != "")
        )
        for product_id, language_id, tag in tagged.all():
            if (kept := without_new_tag(tag)) != tag:
                await self._session.execute(
                    update(d).where(d.product_id == product_id, d.language_id == language_id).values(tag=kept)
                )
        await self._session.commit()
        return RemoveNewResult(
            product_ids=product_ids, label_removed=label_id is not None, filter_removed=filter_id is not None
        )

    @staticmethod
    def _product_values(form: ProductFormDto) -> dict[str, object]:
        return {
            "sku": form.sku, "price": form.price, "minimum": form.minimum, "subtract": form.subtract,
            "stock_status_id": form.stock_status_id, "manufacturer_id": form.manufacturer_id, "weight": form.weight,
            "length": form.length, "width": form.width, "height": form.height, "sort_order": form.sort_order,
            "status": form.enabled, "image": form.image or None,
        }  # fmt: skip

    async def _write_links(self, product_id: int, form: ProductFormDto, sizes_before: set[int]) -> None:
        s, lang = self._session, self._language_id
        description = {
            name: getattr(form, name)
            for name in (
                "name description tag meta_title meta_description meta_keyword table_size variant notice model_info "
                "characteristics"
            ).split()
        }
        await s.execute(delete(DdProductDescription).where(DdProductDescription.product_id == product_id,
                                                           DdProductDescription.language_id == lang))  # fmt: skip
        await s.execute(insert(DdProductDescription).values(product_id=product_id, language_id=lang, **description))
        await s.execute(delete(product_color).where(product_color.c.product_id == product_id,
                                                    product_color.c.language_id == lang))  # fmt: skip
        if form.color.strip():
            await s.execute(insert(product_color).values(product_id=product_id, language_id=lang, name=form.color))

        for model, column, ids in (
            (DdProductToCategory, "category_id", form.category_ids),
            (DdProductToLabel, "label_id", form.label_ids),
            (DdProductFilter, "filter_id", form.filter_ids),
        ):
            await s.execute(delete(model).where(model.product_id == product_id))
            if ids:
                await s.execute(insert(model), [{"product_id": product_id, column: i} for i in dict.fromkeys(ids)])

        await s.execute(delete(related).where(related.c.product_id == product_id))
        if form.related_ids:
            await s.execute(insert(related), [{"product_id": product_id, "related_id": i} for i in form.related_ids])
        for table, ids in ((related_color, form.related_color_ids), (related_variant, form.related_variant_ids)):
            c = table.c
            await s.execute(delete(table).where((c.product_id == product_id) | (c.related_id == product_id)))
            if ids:
                pairs = [(product_id, i) for i in ids] + [(i, product_id) for i in ids]
                await s.execute(insert(table), [{"product_id": a, "related_id": b} for a, b in pairs])

        await s.execute(delete(DdProductImage).where(DdProductImage.product_id == product_id))
        if form.images:
            await s.execute(
                insert(DdProductImage),
                [{"product_id": product_id, "image": i.image, "sort_order": i.sort_order} for i in form.images],
            )

        await s.execute(delete(DdProductSpecial).where(DdProductSpecial.product_id == product_id))
        if form.specials:
            await s.execute(
                insert(DdProductSpecial),
                [
                    {
                        "product_id": product_id,
                        "customer_group_id": sp.customer_group_id,
                        "priority": sp.priority,
                        "price": sp.price,
                        "percent": sp.percent,
                        "date_start": (sp.date_start or NO_START).isoformat(),
                        "date_end": (sp.date_end or NO_END).isoformat(),
                    }  # fmt: skip
                    for sp in form.specials
                ],
            )

        await s.execute(
            delete(seo_url).where(seo_url.c.query == f"product_id={product_id}", seo_url.c.store_id == STORE_ID)
        )
        if form.seo_keyword:
            await s.execute(
                insert(seo_url).values(
                    store_id=STORE_ID, language_id=lang, query=f"product_id={product_id}", keyword=form.seo_keyword
                )  # fmt: skip
            )
        await self._write_sizes(product_id, form.sizes, sizes_before)
        await s.commit()

    async def _write_sizes(self, product_id: int, sizes: list[ProductSizeEditDto], before: set[int]) -> None:
        """Changes prices, adds new sizes with no stock and removes sizes left out; stock stays as production set it."""
        v, s = DdProductOptionValue, self._session
        wanted = {size.option_value_id: size for size in sizes}
        gone = [i for i in before if i not in wanted]
        if gone:
            await s.execute(delete(v).where(v.product_id == product_id, v.option_value_id.in_(gone)))
        for option_value_id, size in wanted.items():
            if option_value_id in before:
                await s.execute(
                    update(v)
                    .where(v.product_id == product_id, v.option_value_id == option_value_id)
                    .values(price=size.price)
                )
                continue
            option_id = await s.scalar(
                select(DdOptionValueDescription.option_id)
                .where(DdOptionValueDescription.option_value_id == option_value_id)
                .limit(1)
            )
            product_option_id = await s.scalar(
                select(product_option.c.product_option_id).where(
                    product_option.c.product_id == product_id, product_option.c.option_id == option_id
                )
            )
            if product_option_id is None:
                product_option_id = (
                    await s.execute(
                        insert(product_option).values(product_id=product_id, option_id=option_id, value="", required=1)
                    )
                ).inserted_primary_key[0]
            await s.execute(
                insert(v).values(
                    product_option_id=product_option_id,
                    product_id=product_id,
                    option_id=option_id,
                    option_value_id=option_value_id,
                    quantity=0,
                    subtract=True,
                    price=size.price,
                    price_prefix="+",
                    points=0,
                    points_prefix="+",
                    weight=Decimal(0),
                    weight_prefix="+",
                )  # fmt: skip
            )
        # The product's quantity is the sum of its sizes, as production keeps it.
        if sizes or before:
            total = await s.scalar(select(func.coalesce(func.sum(v.quantity), 0)).where(v.product_id == product_id))
            await s.execute(update(DdProduct).where(DdProduct.product_id == product_id).values(quantity=total))
