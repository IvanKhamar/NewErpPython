"""Storefront content of the old admin (Python first, item 50): information pages, FAQ, the blog, and the "Про нас"
and NFT blocks of the home page. Everything the storefront shows as HTML is cleaned before saving."""

import logging
import re
from datetime import datetime
from typing import Literal, Protocol

from pydantic import Field

from newerp.application.catalog.dictionaries import preset_url
from newerp.application.common import Clock, ConflictError, Dto, NotFoundError
from newerp.application.content import clean_html
from newerp.domain.catalog import image_path, seo_keyword
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

PageKind = Literal["information", "faq", "blog-categories", "blog-posts"]


class InformationDto(Dto):
    id: int = 0
    title: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=500_000)
    meta_title: str = Field(default="", max_length=255)
    meta_description: str = Field(default="", max_length=255)
    meta_keyword: str = Field(default="", max_length=255)
    seo_keyword: str = Field(default="", max_length=255)
    sort_order: int = 0
    bottom: bool = False  # a link in the storefront's footer
    enabled: bool = True


class FaqDto(Dto):
    id: int = 0
    question: str = Field(default="", max_length=255)
    answer: str = Field(default="", max_length=65_000)
    author: str = Field(default="", max_length=64)
    email: str = Field(default="", max_length=96)
    sort_order: int = 0
    enabled: bool = True


class BlogCategoryDto(Dto):
    id: int = 0
    name: str = Field(default="", max_length=255)
    enabled: bool = True
    posts: int = 0


class BlogPostDto(Dto):
    id: int = 0
    name: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=500_000)
    meta_title: str = Field(default="", max_length=255)
    meta_description: str = Field(default="", max_length=255)
    image_alt: str = Field(default="", max_length=255)
    image: str = Field(default="", max_length=255)
    big_image: str = Field(default="", max_length=255)
    top: bool = False
    enabled: bool = True
    date_available: datetime | None = None  # shown from then; empty: now
    seo_keyword: str = Field(default="", max_length=255)
    category_ids: list[int] = Field(default_factory=list, max_length=50)


# ── The home page blocks: settings of the storefront modules about and nft (dd_setting) ──


class AboutItemDto(Dto):
    image_left: str = Field(default="", max_length=255)
    image_right: str = Field(default="", max_length=255)
    image_mobile: str = Field(default="", max_length=255)
    sort_order: int = 0


class AboutBlockDto(Dto):
    enabled: bool = False
    heading_title: str = Field(default="", max_length=255)
    subtitle: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=65_000)
    items: list[AboutItemDto] = Field(default_factory=list, max_length=50)


class NftImageDto(Dto):
    image: str = Field(default="", max_length=255)
    link: str = Field(default="", max_length=255)
    sort_order: int = 0


class NftItemDto(Dto):
    title: str = Field(default="", max_length=255)
    subtitle: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=65_000)
    sort_order: int = 0


class NftBlockDto(Dto):
    enabled: bool = False
    heading_title: str = Field(default="", max_length=255)
    subtitle: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=65_000)
    heading_form: str = Field(default="", max_length=255)
    subtitle_form: str = Field(default="", max_length=255)
    link: str = Field(default="", max_length=255)
    text_link: str = Field(default="", max_length=255)
    image: str = Field(default="", max_length=255)
    images: list[NftImageDto] = Field(default_factory=list, max_length=50)
    items: list[NftItemDto] = Field(default_factory=list, max_length=50)


class PageStore(Protocol):
    async def information(self) -> list[InformationDto]: ...

    async def save_information(self, page: InformationDto) -> int: ...

    async def faqs(self) -> list[FaqDto]: ...

    async def save_faq(self, faq: FaqDto, now: datetime) -> int: ...

    async def blog_categories(self) -> list[BlogCategoryDto]: ...

    async def save_blog_category(self, category: BlogCategoryDto, now: datetime) -> int: ...

    async def blog_posts(self) -> list[BlogPostDto]: ...

    async def save_blog_post(self, post: BlogPostDto, now: datetime) -> int: ...

    async def delete(self, kind: PageKind, item_id: int) -> bool:
        """With its names, links and SEO address; False when there was none."""
        ...

    async def seo_keyword_owner(self, keyword: str) -> str | None: ...

    async def about(self) -> AboutBlockDto: ...

    async def save_about(self, block: AboutBlockDto) -> None: ...

    async def nft(self) -> NftBlockDto: ...

    async def save_nft(self, block: NftBlockDto) -> None: ...


class PageService:
    def __init__(self, store: PageStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def information(self) -> list[InformationDto]:
        return await self._store.information()

    async def save_information(self, page_id: int | None, page: InformationDto) -> InformationDto:
        await self._exists(page_id, await self._store.information())
        keyword = await self._unique_keyword(page.seo_keyword, f"information_id={page_id}")
        clean = page.model_copy(
            update={
                "id": page_id or 0,
                "title": _required(page.title, "заголовок"),
                "description": clean_html(page.description),
                "seo_keyword": keyword,
            }
        )
        saved = await self._store.save_information(clean)
        return next(p for p in await self._store.information() if p.id == saved)

    async def faqs(self) -> list[FaqDto]:
        return await self._store.faqs()

    async def save_faq(self, faq_id: int | None, faq: FaqDto) -> FaqDto:
        await self._exists(faq_id, await self._store.faqs())
        email = faq.email.strip()
        if email and not _EMAIL.fullmatch(email):
            raise DomainError(f"E-mail «{email}» записано з помилкою.")
        clean = faq.model_copy(
            update={
                "id": faq_id or 0,
                "question": _required(faq.question, "питання"),
                "answer": clean_html(faq.answer),
                "author": faq.author.strip(),
                "email": email,
            }
        )
        saved = await self._store.save_faq(clean, self._clock.now().replace(microsecond=0))
        return next(f for f in await self._store.faqs() if f.id == saved)

    async def blog_categories(self) -> list[BlogCategoryDto]:
        return await self._store.blog_categories()

    async def save_blog_category(self, category_id: int | None, category: BlogCategoryDto) -> BlogCategoryDto:
        await self._exists(category_id, await self._store.blog_categories())
        clean = category.model_copy(update={"id": category_id or 0, "name": _required(category.name, "назву")})
        saved = await self._store.save_blog_category(clean, self._clock.now().replace(microsecond=0))
        return next(c for c in await self._store.blog_categories() if c.id == saved)

    async def blog_posts(self) -> list[BlogPostDto]:
        return await self._store.blog_posts()

    async def save_blog_post(self, post_id: int | None, post: BlogPostDto) -> BlogPostDto:
        await self._exists(post_id, await self._store.blog_posts())
        image, big_image = image_path(post.image), image_path(post.big_image)
        known = {c.id for c in await self._store.blog_categories()}
        unknown = set(post.category_ids) - known
        if unknown:
            raise DomainError(f"Невідомі рубрики блогу: {', '.join(map(str, sorted(unknown)))}.")
        keyword = await self._unique_keyword(post.seo_keyword, f"post_id={post_id}")
        now = self._clock.now().replace(microsecond=0)
        clean = post.model_copy(
            update={
                "id": post_id or 0,
                "name": _required(post.name, "назву"),
                "description": clean_html(post.description),
                "image": image,
                "big_image": big_image,
                "seo_keyword": keyword,
                "date_available": post.date_available or now,
                "category_ids": list(dict.fromkeys(post.category_ids)),
            }
        )
        saved = await self._store.save_blog_post(clean, now)
        return next(p for p in await self._store.blog_posts() if p.id == saved)

    async def delete(self, kind: PageKind, item_id: int, user_id: int) -> None:
        if not await self._store.delete(kind, item_id):
            raise NotFoundError(f"Запису {item_id} немає.")
        audit.info("%s deleted: %s, by user %s", kind, item_id, user_id)

    async def about(self) -> AboutBlockDto:
        return await self._store.about()

    async def save_about(self, block: AboutBlockDto) -> AboutBlockDto:
        items = [
            i.model_copy(
                update={name: image_path(getattr(i, name)) for name in ("image_left", "image_right", "image_mobile")}
            )
            for i in block.items
        ]
        await self._store.save_about(
            block.model_copy(update={"description": clean_html(block.description), "items": items})
        )
        return await self._store.about()

    async def nft(self) -> NftBlockDto:
        return await self._store.nft()

    async def save_nft(self, block: NftBlockDto) -> NftBlockDto:
        clean = block.model_copy(
            update={
                "description": clean_html(block.description),
                "image": image_path(block.image),
                "link": preset_url(block.link) if block.link.strip() else "",
                "images": [
                    i.model_copy(
                        update={"image": image_path(i.image), "link": preset_url(i.link) if i.link.strip() else ""}
                    )
                    for i in block.images
                ],
                "items": [i.model_copy(update={"description": clean_html(i.description)}) for i in block.items],
            }
        )
        await self._store.save_nft(clean)
        return await self._store.nft()

    async def _unique_keyword(self, value: str, own_query: str) -> str:
        keyword = seo_keyword(value)
        if keyword:
            owner = await self._store.seo_keyword_owner(keyword)
            if owner is not None and owner != own_query:
                raise ConflictError(f"SEO-адресу «{keyword}» вже має {owner}.")
        return keyword

    @staticmethod
    async def _exists(item_id: int | None, items: list) -> None:
        if item_id is not None and item_id not in {item.id for item in items}:
            raise NotFoundError(f"Запису {item_id} немає.")


def _required(value: str, what: str) -> str:
    text = value.strip()
    if not text:
        raise DomainError(f"Вкажіть {what}.")
    return text
