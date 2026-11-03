"""Banners and SEO addresses of the storefront (Python first, item 70).

A banner's pictures carry links the storefront puts into href and src, so a link is only the shop's own path or an
https address, and a picture only a path below catalog/. An SEO address points to one known kind of page."""

import logging
import re
from typing import Protocol

from pydantic import Field

from newerp.application.catalog.dictionaries import preset_url
from newerp.application.common import ConflictError, Dto, NotFoundError, PagedResult, normalize_paging
from newerp.domain.catalog import image_path, seo_keyword
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")
SEO_QUERY = re.compile(r"(product_id|category_id|information_id|manufacturer_id|post_id|post_category_id)=[1-9]\d{0,9}")


class BannerImageDto(Dto):
    title: str = Field(default="", max_length=64)
    link: str = Field(default="", max_length=255)
    image: str = Field(default="", max_length=255)
    sort_order: int = 0


class BannerDto(Dto):
    id: int = 0
    name: str = Field(default="", max_length=64)
    enabled: bool = True
    images: list[BannerImageDto] = Field(default_factory=list, max_length=50)
    categories: int = 0  # categories that show it (read only)


class SeoUrlDto(Dto):
    id: int = 0
    query: str = Field(default="", max_length=255)  # "product_id=42"
    keyword: str = Field(default="", max_length=255)


class BannerStore(Protocol):
    async def banners(self) -> list[BannerDto]: ...

    async def save(self, banner: BannerDto) -> int: ...

    async def delete(self, banner_id: int) -> None: ...


class SeoUrlStore(Protocol):
    async def page(self, search: str | None, page: int, page_size: int) -> PagedResult[SeoUrlDto]: ...

    async def find(self, seo_url_id: int) -> SeoUrlDto | None: ...

    async def owner(self, keyword: str) -> SeoUrlDto | None: ...

    async def query_owner(self, query: str) -> SeoUrlDto | None: ...

    async def save(self, item: SeoUrlDto) -> int: ...

    async def delete(self, seo_url_id: int) -> None: ...


class BannerService:
    def __init__(self, store: BannerStore) -> None:
        self._store = store

    async def banners(self) -> list[BannerDto]:
        return await self._store.banners()

    async def save(self, banner_id: int | None, banner: BannerDto, user_id: int) -> BannerDto:
        if banner_id is not None and banner_id not in {b.id for b in await self._store.banners()}:
            raise NotFoundError(f"Банера {banner_id} немає.")
        name = banner.name.strip()
        if not name:
            raise DomainError("Вкажіть назву банера.")
        images = []
        for image in banner.images:
            if not image.image.strip():
                continue
            link = preset_url(image.link) if image.link.strip() else ""
            images.append(image.model_copy(update={"image": image_path(image.image), "link": link,
                                                   "title": image.title.strip()}))  # fmt: skip
        saved = await self._store.save(banner.model_copy(update={"id": banner_id or 0, "name": name, "images": images}))
        audit.info("banner saved: %s, by user %s", saved, user_id)
        return next(b for b in await self._store.banners() if b.id == saved)

    async def delete(self, banner_id: int, user_id: int) -> None:
        banner = next((b for b in await self._store.banners() if b.id == banner_id), None)
        if banner is None:
            raise NotFoundError(f"Банера {banner_id} немає.")
        if banner.categories:
            raise DomainError(f"Банер показують {banner.categories} категорій: спершу приберіть його з них.")
        await self._store.delete(banner_id)
        audit.info("banner deleted: %s, by user %s", banner_id, user_id)


class SeoUrlService:
    def __init__(self, store: SeoUrlStore) -> None:
        self._store = store

    async def page(self, search: str | None, page: int = 1, page_size: int = 50) -> PagedResult[SeoUrlDto]:
        page, page_size = normalize_paging(page, page_size)
        return await self._store.page((search or "").strip() or None, page, page_size)

    async def save(self, seo_url_id: int | None, item: SeoUrlDto, user_id: int) -> SeoUrlDto:
        if seo_url_id is not None and await self._store.find(seo_url_id) is None:
            raise NotFoundError(f"SEO-адреси {seo_url_id} немає.")
        query = item.query.strip()
        if not SEO_QUERY.fullmatch(query):
            raise DomainError("Сторінка: product_id=N, category_id=N, information_id=N, manufacturer_id=N, post_id=N.")
        keyword = seo_keyword(item.keyword)
        if not keyword:
            raise DomainError("Вкажіть адресу.")
        for other in (await self._store.owner(keyword), await self._store.query_owner(query)):
            if other is not None and other.id != seo_url_id:
                raise ConflictError(f"Вже є: «{other.keyword}» → {other.query}.")
        saved = await self._store.save(
            item.model_copy(update={"id": seo_url_id or 0, "query": query, "keyword": keyword})
        )
        audit.info("seo url saved: %s, by user %s", saved, user_id)
        found = await self._store.find(saved)
        assert found is not None
        return found

    async def delete(self, seo_url_id: int, user_id: int) -> None:
        if await self._store.find(seo_url_id) is None:
            raise NotFoundError(f"SEO-адреси {seo_url_id} немає.")
        await self._store.delete(seo_url_id)
        audit.info("seo url deleted: %s, by user %s", seo_url_id, user_id)
