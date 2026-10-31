"""Partners (dd_partner and its map tables) and the catalog data their feed needs."""

from collections import defaultdict

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    exists,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import relationship

from newerp.application.partners import (
    PartnerCategoryMapDto,
    PartnerDto,
    PartnerListItemDto,
    PartnerProductMapDto,
    PartnerProductPickDto,
    ProductSizePriceDto,
    ShopCategoryDto,
)
from newerp.domain.partners import FeedProduct, FeedProductOption, Partner, PartnerCategoryMap, PartnerProductMap
from newerp.infrastructure.db import Base
from newerp.infrastructure.models.catalog import (
    DdCategoryDescription,
    DdOptionDescription,
    DdOptionValueDescription,
    DdProduct,
    DdProductDescription,
    DdProductImage,
    DdProductOptionValue,
    DdProductToCategory,
)

# The old module treats as sizes the options whose name contains "Розмір".
SIZE_OPTION_NAME = "Розмір"

partners = Table(
    "dd_partner",
    Base.metadata,
    Column("partner_id", Integer, primary_key=True),
    Column("code", String(32)),
    Column("name", String(128)),
    Column("status", Boolean),
    Column("feed_url", String(255)),
    Column("feed_enabled", Boolean),
    Column("vendor_name", String(64)),
    Column("notes", Text),
    Column("default_quantity", Integer),
    Column("date_added", DateTime),
    Column("date_modified", DateTime),
)

# MyISAM tables without foreign keys; the ForeignKey here only tells SQLAlchemy how the rows join.
category_map = Table(
    "dd_partner_category_map",
    Base.metadata,
    Column("partner_category_map_id", Integer, primary_key=True),
    Column("partner_id", Integer, ForeignKey("dd_partner.partner_id")),
    Column("category_id", Integer),
    Column("size_name", String(64)),
    Column("external_id", String(64)),
    Column("external_name", String(255)),
    Column("percent_supply", Numeric(8, 2)),
    Column("percent_sale", Numeric(8, 2)),
)

product_map = Table(
    "dd_partner_product_map",
    Base.metadata,
    Column("partner_product_map_id", Integer, primary_key=True),
    Column("partner_id", Integer, ForeignKey("dd_partner.partner_id")),
    Column("product_id", Integer),
    Column("size_name", String(64)),
    Column("external_id", String(64)),
    Column("external_name", String(255)),
    Column("price_supply", Numeric(15, 4)),
    Column("price_sale", Numeric(15, 4)),
)

Base.registry.map_imperatively(
    PartnerCategoryMap, category_map, properties={"id": category_map.c.partner_category_map_id}
)
Base.registry.map_imperatively(PartnerProductMap, product_map, properties={"id": product_map.c.partner_product_map_id})
Base.registry.map_imperatively(
    Partner,
    partners,
    properties={
        "id": partners.c.partner_id,
        "is_active": partners.c.status,
        # delete-orphan: a row removed from the list is deleted, and deleting the partner deletes its rows.
        "_category_map": relationship(
            PartnerCategoryMap,
            order_by=category_map.c.partner_category_map_id,
            cascade="all, delete-orphan",
            lazy="selectin",
        ),
        "_product_map": relationship(
            PartnerProductMap,
            order_by=product_map.c.partner_product_map_id,
            cascade="all, delete-orphan",
            lazy="selectin",
        ),
    },
)


class SqlPartnerRepository:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def list_all(self) -> list[PartnerListItemDto]:
        p = partners.c
        categories = (
            select(func.count()).where(category_map.c.partner_id == p.partner_id).correlate(partners).scalar_subquery()
        )
        products = (
            select(func.count()).where(product_map.c.partner_id == p.partner_id).correlate(partners).scalar_subquery()
        )
        rows = await self._session.execute(
            select(
                p.partner_id, p.code, p.name, p.vendor_name, p.feed_enabled, p.status, categories, products
            ).order_by(p.name)
        )
        return [
            PartnerListItemDto(
                id=r[0],
                code=r[1],
                name=r[2],
                vendor_name=r[3],
                feed_enabled=r[4],
                is_active=r[5],
                categories=r[6],
                products=r[7],
            )  # fmt: skip
            for r in rows
        ]

    async def get_dto(self, partner_id: int) -> PartnerDto | None:
        partner = await self.get(partner_id)
        if partner is None:
            return None
        category_names = await self.get_category_names({r.category_id for r in partner.category_map})
        product_ids = {r.product_id for r in partner.product_map}
        rows = await self._session.execute(
            select(DdProduct.product_id, func.coalesce(DdProductDescription.name, ""), DdProduct.status)
            .outerjoin(DdProductDescription, self._description_of(DdProduct.product_id))
            .where(DdProduct.product_id.in_(list(product_ids)))
        )
        products = {product_id: (name, status) for product_id, name, status in rows}
        return PartnerDto(
            id=partner.id,
            code=partner.code,
            name=partner.name,
            is_active=partner.is_active,
            feed_url=partner.feed_url,
            feed_enabled=partner.feed_enabled,
            vendor_name=partner.vendor_name,
            notes=partner.notes,
            default_quantity=partner.default_quantity,
            date_modified=partner.date_modified,
            category_map=[
                PartnerCategoryMapDto(
                    id=r.id,
                    category_id=r.category_id,
                    category_name=category_names.get(r.category_id, ""),
                    size_name=r.size_name,
                    external_id=r.external_id,
                    external_name=r.external_name,
                    percent_supply=r.percent_supply,
                    percent_sale=r.percent_sale,
                )  # fmt: skip
                for r in partner.category_map
            ],
            product_map=[
                PartnerProductMapDto(
                    id=r.id,
                    product_id=r.product_id,
                    product_name=products.get(r.product_id, ("", False))[0],
                    product_enabled=products.get(r.product_id, ("", False))[1],
                    size_name=r.size_name,
                    external_id=r.external_id,
                    external_name=r.external_name,
                    price_supply=r.price_supply,
                    price_sale=r.price_sale,
                )  # fmt: skip
                for r in partner.product_map
            ],
        )

    async def get(self, partner_id: int) -> Partner | None:
        return await self._session.get(Partner, partner_id)

    async def get_by_code(self, code: str) -> Partner | None:
        return await self._session.scalar(select(Partner).where(partners.c.code == code))

    async def code_exists(self, code: str, except_id: int | None) -> bool:
        query = select(partners.c.partner_id).where(partners.c.code == code)
        if except_id is not None:
            query = query.where(partners.c.partner_id != except_id)
        return await self._session.scalar(select(exists(query))) or False

    def add(self, partner: Partner) -> None:
        self._session.add(partner)

    async def delete(self, partner: Partner) -> None:
        await self._session.delete(partner)

    async def save_changes(self) -> None:
        await self._session.commit()

    async def list_categories(self) -> list[ShopCategoryDto]:
        categories = await self._session.execute(
            select(DdCategoryDescription.category_id, DdCategoryDescription.name)
            .where(DdCategoryDescription.language_id == self._language_id)
            .order_by(DdCategoryDescription.name)
        )
        sizes = self._size_values().subquery()
        rows = await self._session.execute(
            select(DdProductToCategory.category_id, sizes.c.name)
            .join(sizes, sizes.c.product_id == DdProductToCategory.product_id)
            .distinct()
        )
        sizes_by_category: dict[int, set[str]] = defaultdict(set)
        for category_id, name in rows:
            sizes_by_category[category_id].add(name)
        return [
            ShopCategoryDto(id=category_id, name=name, sizes=sorted(sizes_by_category[category_id]))
            for category_id, name in categories
        ]

    async def search_products(self, term: str, limit: int) -> list[PartnerProductPickDto]:
        product_id = int(term) if term.isdigit() else 0
        products = (
            await self._session.execute(
                select(DdProduct.product_id, DdProductDescription.name, DdProduct.status, DdProduct.price)
                .join(DdProductDescription, DdProductDescription.product_id == DdProduct.product_id)
                .where(
                    DdProductDescription.language_id == self._language_id,
                    DdProductDescription.name.contains(term, autoescape=True) | (DdProduct.product_id == product_id),
                )
                .order_by(DdProductDescription.name)
                .limit(limit)
            )
        ).all()
        sizes = self._size_values().subquery()
        size_rows = await self._session.execute(
            select(sizes.c.product_id, sizes.c.name, sizes.c.price).where(
                sizes.c.product_id.in_([p.product_id for p in products])
            )
        )
        sizes_by_product: dict[int, dict[str, ProductSizePriceDto]] = defaultdict(dict)
        for row_product_id, name, price in size_rows:
            sizes_by_product[row_product_id].setdefault(name, ProductSizePriceDto(name=name, price=price))
        return [
            PartnerProductPickDto(
                id=p.product_id,
                name=p.name,
                is_enabled=p.status,
                sizes=[s for _, s in sorted(sizes_by_product[p.product_id].items())]
                or [ProductSizePriceDto(name="", price=p.price)],
            )
            for p in products
        ]

    async def get_feed_products(self, category_ids: set[int], product_ids: set[int]) -> list[FeedProduct]:
        in_categories = exists().where(
            DdProductToCategory.product_id == DdProduct.product_id,
            DdProductToCategory.category_id.in_(list(category_ids)),
        )
        # Selected columns, not whole dd_product rows: so OpenCart's zero dates are not read.
        products = (
            await self._session.execute(
                select(
                    DdProduct.product_id,
                    DdProduct.model,
                    DdProduct.sku,
                    DdProduct.price,
                    DdProduct.image,
                    func.coalesce(DdProductDescription.name, ""),
                    func.coalesce(DdProductDescription.description, ""),
                )
                .outerjoin(DdProductDescription, self._description_of(DdProduct.product_id))
                .where(DdProduct.status.is_(True), DdProduct.product_id.in_(list(product_ids)) | in_categories)
            )
        ).all()
        if not products:
            return []
        ids = [p[0] for p in products]

        categories: dict[int, list[int]] = defaultdict(list)
        for product_id, category_id in await self._session.execute(
            select(DdProductToCategory.product_id, DdProductToCategory.category_id)
            .where(DdProductToCategory.product_id.in_(ids))
            .order_by(DdProductToCategory.category_id)
        ):
            categories[product_id].append(category_id)

        images: dict[int, list[str]] = defaultdict(list)
        for product_id, image in await self._session.execute(
            select(DdProductImage.product_id, DdProductImage.image)
            .where(DdProductImage.product_id.in_(ids))
            .order_by(DdProductImage.sort_order, DdProductImage.product_image_id)
        ):
            images[product_id].append(image or "")

        options: dict[int, list[FeedProductOption]] = defaultdict(list)
        v, d = DdProductOptionValue, DdOptionValueDescription
        for product_id, option_value_id, name, price in await self._session.execute(
            select(v.product_id, v.option_value_id, func.coalesce(d.name, ""), v.price)
            .outerjoin(d, (d.option_value_id == v.option_value_id) & (d.language_id == self._language_id))
            .where(v.product_id.in_(ids))
            .order_by(v.option_value_id, v.product_option_value_id)
        ):
            options[product_id].append(FeedProductOption(option_value_id, name, price))

        return [
            FeedProduct(
                product_id=product_id,
                model=model,
                sku=sku,
                price=price,
                name=name,
                description=description,
                category_ids=categories[product_id],
                images=([image] if image else []) + images[product_id],
                options=options[product_id],
            )
            for product_id, model, sku, price, image, name, description in products
        ]

    async def get_category_names(self, category_ids: set[int]) -> dict[int, str]:
        rows = await self._session.execute(
            select(DdCategoryDescription.category_id, DdCategoryDescription.name).where(
                DdCategoryDescription.language_id == self._language_id,
                DdCategoryDescription.category_id.in_(list(category_ids)),
            )
        )
        return {category_id: name for category_id, name in rows}

    def _description_of(self, product_id_column):
        return (DdProductDescription.product_id == product_id_column) & (
            DdProductDescription.language_id == self._language_id
        )

    def _size_values(self):
        """Product sizes: values of options whose name contains "Розмір", with the size price."""
        v, od, vd = DdProductOptionValue, DdOptionDescription, DdOptionValueDescription
        return (
            select(v.product_id, vd.name, v.price)
            .join(od, od.option_id == v.option_id)
            .join(vd, vd.option_value_id == v.option_value_id)
            .where(
                od.language_id == self._language_id,
                vd.language_id == self._language_id,
                od.name.contains(SIZE_OPTION_NAME),
            )
        )
