"""Information pages, FAQ, the blog and the home page blocks over the storefront's tables.

The about and nft blocks are OpenCart module settings: rows of dd_setting whose arrays are JSON (serialized = 1),
with texts per language. Only this language's texts are replaced; the others stay as they were."""

import json
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    case,
    delete,
    func,
    insert,
    select,
)
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.catalog.pages import (
    AboutBlockDto,
    AboutItemDto,
    BlogCategoryDto,
    BlogPostDto,
    FaqDto,
    InformationDto,
    NftBlockDto,
    NftImageDto,
    NftItemDto,
    PageKind,
)
from newerp.infrastructure.catalog.product_edit import STORE_ID, seo_url
from newerp.infrastructure.models.settings import DdSetting

metadata = MetaData()
information = Table(
    "dd_information",
    metadata,
    Column("information_id", Integer, primary_key=True),
    Column("bottom", Integer),
    Column("sort_order", Integer),
    Column("status", Boolean),
)
information_description = Table(
    "dd_information_description",
    metadata,
    Column("information_id", Integer, primary_key=True),
    Column("language_id", Integer, primary_key=True),
    Column("title", String(64)),
    Column("description", Text),
    Column("meta_title", String(255)),
    Column("meta_description", String(255)),
    Column("meta_keyword", String(255)),
)
information_to_store = Table(
    "dd_information_to_store",
    metadata,
    Column("information_id", Integer, primary_key=True),
    Column("store_id", Integer, primary_key=True),
)
faq = Table(
    "dd_faq",
    metadata,
    Column("faq_id", Integer, primary_key=True),
    Column("author", String(64)),
    Column("email", String(96)),
    Column("comment", Text),
    Column("sort_order", Integer),
    Column("status", Boolean),
    Column("date_added", DateTime),
)
faq_description = Table(
    "dd_faq_description",
    metadata,
    Column("faq_id", Integer, primary_key=True),
    Column("language_id", Integer, primary_key=True),
    Column("name", String(255)),
    Column("description", Text),
)
post = Table(
    "dd_post",
    metadata,
    Column("post_id", Integer, primary_key=True),
    Column("image", String(255)),
    Column("big_image", String(255)),
    Column("is_top", Boolean),
    Column("status", Boolean),
    Column("date_available", DateTime),
    Column("date_added", DateTime),
)
post_description = Table(
    "dd_post_description",
    metadata,
    Column("post_id", Integer, primary_key=True),
    Column("language_id", Integer, primary_key=True),
    Column("name", String(255)),
    Column("description", Text),
    Column("meta_title", String(255)),
    Column("meta_description", String(255)),
    Column("image_alt", String(255)),
)
post_category = Table(
    "dd_post_category",
    metadata,
    Column("post_category_id", Integer, primary_key=True),
    Column("status", Boolean),
    Column("date_added", DateTime),
)
post_category_description = Table(
    "dd_post_category_description",
    metadata,
    Column("post_category_id", Integer, primary_key=True),
    Column("language_id", Integer, primary_key=True),
    Column("name", String(255)),
)
post_to_category = Table(
    "dd_post_to_category",
    metadata,
    Column("post_id", Integer, primary_key=True),
    Column("post_category_id", Integer, primary_key=True),
)

# What a page delete removes: its own row and every row pointing to it, plus its SEO address.
DELETES: dict[str, tuple[tuple[Table, str], ...]] = {
    "information": (
        (information_description, "information_id"),
        (information_to_store, "information_id"),
        (information, "information_id"),
    ),
    "faq": ((faq_description, "faq_id"), (faq, "faq_id")),
    "blog-categories": (
        (post_category_description, "post_category_id"),
        (post_to_category, "post_category_id"),
        (post_category, "post_category_id"),
    ),
    "blog-posts": ((post_description, "post_id"), (post_to_category, "post_id"), (post, "post_id")),
}
SEO_QUERIES = {"information": "information_id", "blog-posts": "post_id"}


def _real_date(column):
    """The old site may hold zero dates, which no Python date can read: they come back empty."""
    return case((column < "1000-01-02", None), else_=column)


class SqlPageStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    # Information pages

    async def information(self) -> list[InformationDto]:
        i, d = information.c, information_description.c
        rows = await self._session.execute(
            select(
                i.information_id,
                i.bottom,
                i.sort_order,
                i.status,
                d.title,
                d.description,
                d.meta_title,
                d.meta_description,
                d.meta_keyword,
            )
            .outerjoin(
                information_description, (d.information_id == i.information_id) & (d.language_id == self._language_id)
            )
            .order_by(i.sort_order, d.title)
        )
        keywords = await self._keywords("information_id")
        return [
            InformationDto(
                id=r[0],
                bottom=bool(r[1]),
                sort_order=r[2],
                enabled=r[3],
                title=r[4] or "",
                description=r[5] or "",
                meta_title=r[6] or "",
                meta_description=r[7] or "",
                meta_keyword=r[8] or "",
                seo_keyword=keywords.get(r[0], ""),
            )
            for r in rows
        ]

    async def save_information(self, page: InformationDto) -> int:
        values = {"bottom": int(page.bottom), "sort_order": page.sort_order, "status": page.enabled}
        page_id = await self._upsert(information, "information_id", page.id, values)
        if not page.id:
            await self._session.execute(insert(information_to_store).values(information_id=page_id, store_id=STORE_ID))
        await self._names(
            information_description,
            "information_id",
            page_id,
            title=page.title,
            description=page.description,
            meta_title=page.meta_title,
            meta_description=page.meta_description,
            meta_keyword=page.meta_keyword,
        )
        await self._seo("information_id", page_id, page.seo_keyword)
        await self._session.commit()
        return page_id

    # FAQ

    async def faqs(self) -> list[FaqDto]:
        f, d = faq.c, faq_description.c
        rows = await self._session.execute(
            select(f.faq_id, d.name, d.description, f.author, f.email, f.sort_order, f.status)
            .outerjoin(faq_description, (d.faq_id == f.faq_id) & (d.language_id == self._language_id))
            .order_by(f.sort_order, f.faq_id)
        )
        return [
            FaqDto(
                id=r[0], question=r[1] or "", answer=r[2] or "", author=r[3], email=r[4], sort_order=r[5], enabled=r[6]
            )
            for r in rows
        ]

    async def save_faq(self, item: FaqDto, now: datetime) -> int:
        values = {"author": item.author, "email": item.email, "sort_order": item.sort_order, "status": item.enabled}
        faq_id = await self._upsert(faq, "faq_id", item.id, values, {"comment": "", "date_added": now})
        await self._names(faq_description, "faq_id", faq_id, name=item.question, description=item.answer)
        await self._session.commit()
        return faq_id

    # Blog

    async def blog_categories(self) -> list[BlogCategoryDto]:
        c, d = post_category.c, post_category_description.c
        posts = select(func.count()).where(post_to_category.c.post_category_id == c.post_category_id).scalar_subquery()
        rows = await self._session.execute(
            select(c.post_category_id, d.name, c.status, posts)
            .outerjoin(
                post_category_description,
                (d.post_category_id == c.post_category_id) & (d.language_id == self._language_id),
            )
            .order_by(d.name)
        )
        return [BlogCategoryDto(id=r[0], name=r[1] or "", enabled=r[2], posts=r[3]) for r in rows]

    async def save_blog_category(self, category: BlogCategoryDto, now: datetime) -> int:
        category_id = await self._upsert(
            post_category, "post_category_id", category.id, {"status": category.enabled}, {"date_added": now}
        )
        await self._names(post_category_description, "post_category_id", category_id, name=category.name)
        await self._session.commit()
        return category_id

    async def blog_posts(self) -> list[BlogPostDto]:
        p, d = post.c, post_description.c
        rows = (
            await self._session.execute(
                select(
                    p.post_id,
                    p.image,
                    p.big_image,
                    p.is_top,
                    p.status,
                    _real_date(p.date_available),
                    d.name,
                    d.description,
                    d.meta_title,
                    d.meta_description,
                    d.image_alt,
                )
                .outerjoin(post_description, (d.post_id == p.post_id) & (d.language_id == self._language_id))
                .order_by(p.date_available.desc(), p.post_id.desc())
            )
        ).all()
        links: dict[int, list[int]] = {}
        for post_id, category_id in await self._session.execute(select(post_to_category)):
            links.setdefault(post_id, []).append(category_id)
        keywords = await self._keywords("post_id")
        return [
            BlogPostDto(
                id=r[0],
                image=r[1] or "",
                big_image=r[2] or "",
                top=r[3],
                enabled=r[4],
                date_available=r[5],
                name=r[6] or "",
                description=r[7] or "",
                meta_title=r[8] or "",
                meta_description=r[9] or "",
                image_alt=r[10] or "",
                seo_keyword=keywords.get(r[0], ""),
                category_ids=links.get(r[0], []),
            )
            for r in rows
        ]

    async def save_blog_post(self, item: BlogPostDto, now: datetime) -> int:
        values = {
            "image": item.image,
            "big_image": item.big_image,
            "is_top": item.top,
            "status": item.enabled,
            "date_available": item.date_available,
        }
        post_id = await self._upsert(post, "post_id", item.id, values, {"date_added": now})
        await self._names(
            post_description,
            "post_id",
            post_id,
            name=item.name,
            description=item.description,
            meta_title=item.meta_title,
            meta_description=item.meta_description,
            image_alt=item.image_alt,
        )
        await self._session.execute(delete(post_to_category).where(post_to_category.c.post_id == post_id))
        if item.category_ids:
            links = [{"post_id": post_id, "post_category_id": i} for i in item.category_ids]
            await self._session.execute(insert(post_to_category), links)
        await self._seo("post_id", post_id, item.seo_keyword)
        await self._session.commit()
        return post_id

    # Shared

    async def delete(self, kind: PageKind, item_id: int) -> bool:
        tables = DELETES[kind]
        own, key = tables[-1]
        if not await self._session.scalar(select(func.count()).where(own.c[key] == item_id)):
            return False
        for table, column in tables:
            await self._session.execute(delete(table).where(table.c[column] == item_id))
        if kind in SEO_QUERIES:
            await self._session.execute(delete(seo_url).where(seo_url.c.query == f"{SEO_QUERIES[kind]}={item_id}"))
        await self._session.commit()
        return True

    async def seo_keyword_owner(self, keyword: str) -> str | None:
        return await self._session.scalar(
            select(seo_url.c.query).where(seo_url.c.keyword == keyword, seo_url.c.store_id == STORE_ID).limit(1)
        )

    async def _upsert(
        self, table: Table, key: str, row_id: int, values: dict[str, Any], on_insert: dict[str, Any] | None = None
    ) -> int:
        if row_id:
            await self._session.execute(sql_update(table).where(table.c[key] == row_id).values(values))
            return row_id
        result = await self._session.execute(insert(table).values({**values, **(on_insert or {})}))
        return result.inserted_primary_key[0]

    async def _names(self, table: Table, key: str, row_id: int, **values: object) -> None:
        c = table.c
        await self._session.execute(delete(table).where(c[key] == row_id, c.language_id == self._language_id))
        await self._session.execute(insert(table).values({key: row_id, "language_id": self._language_id, **values}))

    async def _seo(self, key: str, row_id: int, keyword: str) -> None:
        query = f"{key}={row_id}"
        await self._session.execute(delete(seo_url).where(seo_url.c.query == query, seo_url.c.store_id == STORE_ID))
        if keyword:
            await self._session.execute(
                insert(seo_url).values(store_id=STORE_ID, language_id=self._language_id, query=query, keyword=keyword)
            )

    async def _keywords(self, key: str) -> dict[int, str]:
        rows = await self._session.execute(
            select(seo_url.c.query, seo_url.c.keyword).where(
                seo_url.c.query.like(f"{key}=%"), seo_url.c.store_id == STORE_ID
            )
        )
        return {int(query.split("=", 1)[1]): keyword for query, keyword in rows if query.split("=", 1)[1].isdigit()}

    # The home page blocks (module settings)

    async def _settings(self, code: str) -> dict[str, Any]:
        rows = await self._session.execute(
            select(DdSetting.key, DdSetting.value, DdSetting.serialized).where(
                DdSetting.store_id == STORE_ID, DdSetting.code == code
            )
        )
        result: dict[str, Any] = {}
        for key, value, serialized in rows:
            try:
                result[key] = json.loads(value) if serialized else value
            except ValueError:
                result[key] = {}  # a broken value of the old site is read as empty and replaced on the next save
        return result

    async def _save_settings(self, code: str, values: dict[str, Any]) -> None:
        await self._session.execute(delete(DdSetting).where(DdSetting.store_id == STORE_ID, DdSetting.code == code))
        rows = [
            {
                "store_id": STORE_ID,
                "code": code,
                "key": key,
                "value": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                "serialized": not isinstance(value, str),
            }
            for key, value in values.items()
        ]
        await self._session.execute(insert(DdSetting), rows)
        await self._session.commit()

    def _texts(self, by_language: Any) -> dict[str, Any]:
        texts = by_language.get(str(self._language_id)) if isinstance(by_language, dict) else None
        return texts if isinstance(texts, dict) else {}

    def _with_texts(self, by_language: Any, texts: dict[str, Any]) -> dict[str, Any]:
        merged = dict(by_language) if isinstance(by_language, dict) else {}
        merged[str(self._language_id)] = texts
        return merged

    async def about(self) -> AboutBlockDto:
        s = await self._settings("module_about")
        texts = self._texts(s.get("module_about_description"))
        items = s.get("module_about_item") or []
        items = list(items.values()) if isinstance(items, dict) else items
        return AboutBlockDto(
            enabled=s.get("module_about_status") == "1",
            heading_title=texts.get("heading_title", ""),
            subtitle=texts.get("subtitle", ""),
            description=texts.get("description", ""),
            items=[
                AboutItemDto(**{k: v for k, v in i.items() if k in AboutItemDto.model_fields})
                for i in items
                if isinstance(i, dict)
            ],
        )

    async def save_about(self, block: AboutBlockDto) -> None:
        s = await self._settings("module_about")
        texts = {"heading_title": block.heading_title, "subtitle": block.subtitle, "description": block.description}
        await self._save_settings(
            "module_about",
            {
                "module_about_status": "1" if block.enabled else "0",
                "module_about_description": self._with_texts(s.get("module_about_description"), texts),
                "module_about_item": [i.model_dump() for i in block.items],
            },
        )

    async def nft(self) -> NftBlockDto:
        s = await self._settings("module_nft")
        texts = self._texts(s.get("module_nft_description"))
        images = s.get("module_nft_images") or []
        items = s.get("module_nft_item") or []
        images = list(images.values()) if isinstance(images, dict) else images
        items = list(items.values()) if isinstance(items, dict) else items
        return NftBlockDto(
            enabled=s.get("module_nft_status") == "1",
            image=s.get("module_nft_image") or "",
            **{
                name: texts.get(name, "")
                for name in (
                    "heading_title",
                    "subtitle",
                    "description",
                    "heading_form",
                    "subtitle_form",
                    "link",
                    "text_link",
                )
            },
            images=[
                NftImageDto(
                    image=i.get("image", ""),
                    sort_order=int(i.get("sort_order") or 0),
                    link=self._texts(i.get("descriptions")).get("link", ""),
                )
                for i in images
                if isinstance(i, dict)
            ],
            items=[
                NftItemDto(
                    sort_order=int(i.get("sort_order") or 0),
                    **{k: self._texts(i.get("descriptions")).get(k, "") for k in ("title", "subtitle", "description")},
                )
                for i in items
                if isinstance(i, dict)
            ],
        )

    async def save_nft(self, block: NftBlockDto) -> None:
        s = await self._settings("module_nft")
        texts = block.model_dump(
            include={"heading_title", "subtitle", "description", "heading_form", "subtitle_form", "link", "text_link"}
        )
        await self._save_settings(
            "module_nft",
            {
                "module_nft_status": "1" if block.enabled else "0",
                "module_nft_image": block.image,
                "module_nft_description": self._with_texts(s.get("module_nft_description"), texts),
                "module_nft_images": [
                    {
                        "image": i.image,
                        "sort_order": i.sort_order,
                        "descriptions": {str(self._language_id): {"link": i.link}},
                    }
                    for i in block.images
                ],
                "module_nft_item": [
                    {
                        "sort_order": i.sort_order,
                        "descriptions": {
                            str(self._language_id): {
                                "title": i.title,
                                "subtitle": i.subtitle,
                                "description": i.description,
                            }
                        },
                    }
                    for i in block.items
                ],
            },
        )
