"""Catalog dictionaries of the old admin (Python first): product labels and storefront search presets (item 47),
categories with their tree paths (item 48)."""

import logging
import re
from datetime import datetime
from typing import Protocol

from pydantic import Field

from newerp.application.common import Clock, ConflictError, Dto, LookupItem, NotFoundError
from newerp.application.content import clean_html
from newerp.domain.catalog import image_path, seo_keyword
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")

COLOR = re.compile(r"#[0-9a-fA-F]{6}")  # it goes into a style attribute on the storefront: nothing else may pass


# ── Labels ──


class LabelDto(Dto):
    id: int = 0
    name: str = Field(default="", max_length=64)
    background_color: str = Field(default="", max_length=16)
    text_color: str = Field(default="", max_length=16)
    sort_order: int = 0
    enabled: bool = True
    products: int = 0  # how many products have it (read only)


# ── Search presets ──


class SearchPresetDto(Dto):
    """A ready search on the storefront: a name and the address it opens."""

    id: int = 0
    name: str = Field(default="", max_length=255)
    url: str = Field(default="", max_length=255)
    sort_order: int = 0
    enabled: bool = True


def preset_url(value: str) -> str:
    """Only an address of the shop itself ("/sukni?filter=…") or an https one: the storefront puts it into href,
    so javascript: and data: never pass."""
    url = value.strip()
    if url.startswith("/") and not url.startswith("//") or url.lower().startswith("https://"):
        if not any(c in url for c in "\"'<> \\") and not any(ord(c) < 32 for c in url):
            return url
    raise DomainError("Адреса пресета: відносна (/catalog?...) або https://..., без пробілів і лапок.")


# ── Categories ──


class CategoryListItem(Dto):
    id: int
    name: str  # with the parents: "Одяг > Сукні"
    parent_id: int
    sort_order: int
    enabled: bool
    products: int


class CategoryFormDto(Dto):
    """The OpenCart category with the old site's own fields (production period, banner, size-table picture)."""

    parent_id: int = 0
    name: str = Field(default="", max_length=255)
    product_name: str = Field(default="", max_length=255)
    advertising_product_name: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=65_000)
    description_table_size: str = Field(default="", max_length=65_000)
    meta_title: str = Field(default="", max_length=255)
    meta_description: str = Field(default="", max_length=255)
    meta_keyword: str = Field(default="", max_length=255)
    seo_keyword: str = Field(default="", max_length=255)
    image: str = Field(default="", max_length=255)
    image_table_size: str = Field(default="", max_length=255)
    top: bool = False
    column: int = Field(default=1, ge=0, le=10)
    sort_order: int = 0
    enabled: bool = True
    production_period: int = Field(default=0, ge=0, le=365)
    banner_id: int = 0
    filter_ids: list[int] = Field(default_factory=list, max_length=200)


class CategoryEditDto(CategoryFormDto):
    id: int


class DictionaryStore(Protocol):
    async def labels(self) -> list[LabelDto]: ...

    async def save_label(self, label: LabelDto) -> int: ...

    async def delete_label(self, label_id: int) -> bool:
        """Also takes it off every product; False when there was no such label."""
        ...

    async def presets(self) -> list[SearchPresetDto]: ...

    async def save_preset(self, preset: SearchPresetDto) -> int: ...

    async def delete_preset(self, preset_id: int) -> bool: ...

    async def categories(self) -> list[CategoryListItem]: ...

    async def category(self, category_id: int) -> CategoryEditDto | None: ...

    async def filters(self) -> list[LookupItem]: ...

    async def seo_keyword_owner(self, keyword: str) -> str | None: ...

    async def save_category(self, category_id: int | None, form: CategoryFormDto, now: datetime) -> int:
        """Creates (None) or updates the category and rebuilds its tree path and the paths below it."""
        ...

    async def has_children(self, category_id: int) -> bool: ...

    async def delete_category(self, category_id: int) -> None: ...

    async def repair_paths(self) -> int:
        """Rebuilds dd_category_path from parent_id for every category (the old "repair"); returns their number."""
        ...


class CatalogDictionaryService:
    def __init__(self, store: DictionaryStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    # Labels

    async def labels(self) -> list[LabelDto]:
        return await self._store.labels()

    async def save_label(self, label_id: int | None, label: LabelDto) -> LabelDto:
        name = label.name.strip()
        if not name:
            raise DomainError("Вкажіть назву мітки.")
        for color in (label.background_color, label.text_color):
            if color and not COLOR.fullmatch(color):
                raise DomainError(f"Колір «{color}» має бути на кшталт #ff8800.")
        if label_id is not None and label_id not in {item.id for item in await self._store.labels()}:
            raise NotFoundError(f"Мітки {label_id} немає.")
        saved_id = await self._store.save_label(label.model_copy(update={"id": label_id or 0, "name": name}))
        return next(item for item in await self._store.labels() if item.id == saved_id)

    async def delete_label(self, label_id: int, user_id: int) -> None:
        if not await self._store.delete_label(label_id):
            raise NotFoundError(f"Мітки {label_id} немає.")
        audit.info("label deleted: label %s, by user %s", label_id, user_id)

    # Search presets

    async def presets(self) -> list[SearchPresetDto]:
        return await self._store.presets()

    async def save_preset(self, preset_id: int | None, preset: SearchPresetDto) -> SearchPresetDto:
        name = preset.name.strip()
        if not name:
            raise DomainError("Вкажіть назву пресета.")
        url = preset_url(preset.url)
        if preset_id is not None and preset_id not in {item.id for item in await self._store.presets()}:
            raise NotFoundError(f"Пресета {preset_id} немає.")
        saved_id = await self._store.save_preset(
            preset.model_copy(update={"id": preset_id or 0, "name": name, "url": url})
        )
        return next(item for item in await self._store.presets() if item.id == saved_id)

    async def delete_preset(self, preset_id: int) -> None:
        if not await self._store.delete_preset(preset_id):
            raise NotFoundError(f"Пресета {preset_id} немає.")

    # Categories

    async def categories(self) -> list[CategoryListItem]:
        return await self._store.categories()

    async def filters(self) -> list[LookupItem]:
        return await self._store.filters()

    async def category(self, category_id: int) -> CategoryEditDto:
        category = await self._store.category(category_id)
        if category is None:
            raise NotFoundError(f"Категорії {category_id} немає.")
        return category

    async def save_category(self, category_id: int | None, form: CategoryFormDto, user_id: int) -> CategoryEditDto:
        image, table_image, keyword = (
            image_path(form.image),
            image_path(form.image_table_size),
            seo_keyword(form.seo_keyword),
        )
        if not form.name.strip():
            raise DomainError("Вкажіть назву категорії.")
        if category_id is not None:
            await self.category(category_id)
        known = {item.id: item for item in await self._store.categories()}
        if form.parent_id:
            if form.parent_id not in known:
                raise DomainError(f"Батьківської категорії {form.parent_id} немає.")
            # A category cannot go under itself or under one of its own subcategories: the tree would become a loop.
            parent, seen = form.parent_id, set()
            while parent and parent not in seen:
                if parent == category_id:
                    raise DomainError("Категорію не можна вкласти в саму себе чи в її підкатегорію.")
                seen.add(parent)
                parent = known[parent].parent_id if parent in known else 0
        unknown = set(form.filter_ids) - {item.id for item in await self._store.filters()}
        if unknown:
            raise DomainError(f"Невідомі фільтри: {', '.join(map(str, sorted(unknown)))}.")
        if keyword:
            owner = await self._store.seo_keyword_owner(keyword)
            if owner is not None and owner != f"category_id={category_id}":
                raise ConflictError(f"SEO-адресу «{keyword}» вже має {owner}.")
        clean = form.model_copy(
            update={
                "name": form.name.strip(), "image": image, "image_table_size": table_image, "seo_keyword": keyword,
                "description": clean_html(form.description),
                "description_table_size": clean_html(form.description_table_size),
                "filter_ids": list(dict.fromkeys(form.filter_ids)),
            }
        )  # fmt: skip
        saved_id = await self._store.save_category(category_id, clean, self._clock.now().replace(microsecond=0))
        audit.info("category saved: category %s, by user %s", saved_id, user_id)
        return await self.category(saved_id)

    async def delete_category(self, category_id: int, user_id: int) -> None:
        """Unlike the old admin, subcategories are not deleted along: they must be moved or deleted first."""
        await self.category(category_id)
        if await self._store.has_children(category_id):
            raise DomainError("У категорії є підкатегорії: спершу перенесіть або видаліть їх.")
        await self._store.delete_category(category_id)
        audit.info("category deleted: category %s, by user %s", category_id, user_id)

    async def repair_paths(self) -> int:
        return await self._store.repair_paths()
