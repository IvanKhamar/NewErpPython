"""Banners (dd_banner, dd_banner_image) and SEO addresses (dd_seo_url)."""

from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.banners import BannerDto, BannerImageDto, SeoUrlDto
from newerp.application.common import PagedResult
from newerp.infrastructure.catalog.product_edit import STORE_ID, seo_url
from newerp.infrastructure.models.catalog import DdCategory

metadata = MetaData()
banner = Table(
    "dd_banner", metadata, Column("banner_id", Integer, primary_key=True), Column("name", String(64)),
    Column("status", Boolean),
)  # fmt: skip
banner_image = Table(
    "dd_banner_image", metadata, Column("banner_image_id", Integer, primary_key=True), Column("banner_id", Integer),
    Column("language_id", Integer), Column("title", String(64)), Column("link", String(255)),
    Column("image", String(255)), Column("sort_order", Integer),
)  # fmt: skip


class SqlBannerStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def banners(self) -> list[BannerDto]:
        b, i = banner.c, banner_image.c
        categories = select(func.count()).where(DdCategory.banner_id == b.banner_id).scalar_subquery()
        rows = (await self._session.execute(select(b.banner_id, b.name, b.status, categories).order_by(b.name))).all()
        images: dict[int, list[BannerImageDto]] = {}
        for banner_id, title, link, image, sort_order in await self._session.execute(
            select(i.banner_id, i.title, i.link, i.image, i.sort_order)
            .where(i.language_id == self._language_id)
            .order_by(i.sort_order)
        ):
            images.setdefault(banner_id, []).append(
                BannerImageDto(title=title, link=link, image=image, sort_order=sort_order)
            )
        return [
            BannerDto(id=r[0], name=r[1], enabled=bool(r[2]), images=images.get(r[0], []), categories=r[3])
            for r in rows
        ]

    async def save(self, item: BannerDto) -> int:
        s, b = self._session, banner.c
        values = {"name": item.name, "status": item.enabled}
        if item.id:
            await s.execute(update(banner).where(b.banner_id == item.id).values(values))
            banner_id = item.id
        else:
            banner_id = (await s.execute(insert(banner).values(values))).inserted_primary_key[0]
        i = banner_image.c
        await s.execute(delete(banner_image).where(i.banner_id == banner_id, i.language_id == self._language_id))
        if item.images:
            await s.execute(
                insert(banner_image),
                [
                    {
                        "banner_id": banner_id,
                        "language_id": self._language_id,
                        "title": x.title,
                        "link": x.link,
                        "image": x.image,
                        "sort_order": x.sort_order,
                    }  # fmt: skip
                    for x in item.images
                ],
            )
        await s.commit()
        return banner_id

    async def delete(self, banner_id: int) -> None:
        for table in (banner_image, banner):
            await self._session.execute(delete(table).where(table.c.banner_id == banner_id))
        await self._session.commit()


class SqlSeoUrlStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    def _dto(self, row) -> SeoUrlDto:
        return SeoUrlDto(id=row[0], query=row[1], keyword=row[2])

    async def page(self, search: str | None, page: int, page_size: int) -> PagedResult[SeoUrlDto]:
        u = seo_url.c
        query = select(u.seo_url_id, u.query, u.keyword).where(u.store_id == STORE_ID)
        if search:
            query = query.where(
                or_(u.keyword.contains(search, autoescape=True), u.query.contains(search, autoescape=True))
            )
        total = await self._session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await self._session.execute(query.order_by(u.keyword).offset((page - 1) * page_size).limit(page_size))
        items = [self._dto(r) for r in rows]
        return PagedResult[SeoUrlDto](items=items, page=page, page_size=page_size, total_count=total or 0)

    async def _one(self, condition) -> SeoUrlDto | None:
        u = seo_url.c
        row = (
            await self._session.execute(
                select(u.seo_url_id, u.query, u.keyword).where(condition, u.store_id == STORE_ID).limit(1)
            )
        ).first()
        return None if row is None else self._dto(row)

    async def find(self, seo_url_id: int) -> SeoUrlDto | None:
        return await self._one(seo_url.c.seo_url_id == seo_url_id)

    async def owner(self, keyword: str) -> SeoUrlDto | None:
        return await self._one(seo_url.c.keyword == keyword)

    async def query_owner(self, query: str) -> SeoUrlDto | None:
        return await self._one((seo_url.c.query == query) & (seo_url.c.language_id == self._language_id))

    async def save(self, item: SeoUrlDto) -> int:
        u = seo_url.c
        values = {"query": item.query, "keyword": item.keyword}
        if item.id:
            await self._session.execute(update(seo_url).where(u.seo_url_id == item.id).values(values))
            seo_url_id = item.id
        else:
            query = insert(seo_url).values({**values, "store_id": STORE_ID, "language_id": self._language_id})
            seo_url_id = (await self._session.execute(query)).inserted_primary_key[0]
        await self._session.commit()
        return seo_url_id

    async def delete(self, seo_url_id: int) -> None:
        await self._session.execute(delete(seo_url).where(seo_url.c.seo_url_id == seo_url_id))
        await self._session.commit()
