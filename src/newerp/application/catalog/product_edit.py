"""The whole product form of the old admin (Python first): create, edit, copy, and "remove NEW" of a print.

Stock stays with production: the form changes the size list and size prices, never a size's quantity. A copy starts
disabled, without stock and without the SEO address, which must stay unique."""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from pydantic import Field

from newerp.application.common import Clock, ConflictError, Dto, LookupItem, Money, NotFoundError
from newerp.application.content import clean_html
from newerp.domain.catalog import image_path, seo_keyword
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")

Ids = list[int]
MAX_LINKS = 200


class ProductImageDto(Dto):
    image: str = Field(max_length=255)
    sort_order: int = 0


class ProductSpecialDto(Dto):
    """A sale price: price, or percent off the regular price when price is 0 (the old site's own column)."""

    customer_group_id: int = 1
    priority: int = 1
    price: Money = Field(default=Decimal(0), ge=0, le=10_000_000)
    percent: Money = Field(default=Decimal(0), ge=0, le=100)
    date_start: date | None = None
    date_end: date | None = None


class ProductAttributeDto(Dto):
    """A product's attribute value: "Склад" → "Льон 100%"."""

    attribute_id: int
    text: str = Field(default="", max_length=10_000)


class ProductSizeEditDto(Dto):
    """option_value_id of the size option; quantity is shown only, production keeps it."""

    option_value_id: int
    name: str = ""
    price: Money = Field(default=Decimal(0), ge=0, le=10_000_000)
    quantity: int = 0


class ProductFormDto(Dto):
    """The product as the form edits it. Text fields keep the OpenCart column lengths; HTML ones are cleaned."""

    model: str = Field(default="", max_length=64)
    sku: str = Field(default="", max_length=64)
    price: Money = Field(default=Decimal(0), ge=0, le=10_000_000)
    minimum: int = Field(default=1, ge=1, le=10_000)
    subtract: bool = True
    stock_status_id: int = 0
    manufacturer_id: int = 0
    weight: Money = Field(default=Decimal(0), ge=0, le=100_000)
    length: Money = Field(default=Decimal(0), ge=0, le=10_000)
    width: Money = Field(default=Decimal(0), ge=0, le=10_000)
    height: Money = Field(default=Decimal(0), ge=0, le=10_000)
    sort_order: int = 0
    enabled: bool = False
    image: str = Field(default="", max_length=255)
    name: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=65_000)
    tag: str = Field(default="", max_length=10_000)
    meta_title: str = Field(default="", max_length=255)
    meta_description: str = Field(default="", max_length=255)
    meta_keyword: str = Field(default="", max_length=255)
    table_size: str = Field(default="", max_length=65_000)
    variant: str = Field(default="", max_length=10_000)
    notice: str = Field(default="", max_length=65_000)
    model_info: str = Field(default="", max_length=65_000)
    characteristics: str = Field(default="", max_length=65_000)
    color: str = Field(default="", max_length=255)
    seo_keyword: str = Field(default="", max_length=255)
    category_ids: Ids = Field(default_factory=list, max_length=MAX_LINKS)
    label_ids: Ids = Field(default_factory=list, max_length=MAX_LINKS)
    filter_ids: Ids = Field(default_factory=list, max_length=MAX_LINKS)
    related_ids: Ids = Field(default_factory=list, max_length=MAX_LINKS)
    related_color_ids: Ids = Field(default_factory=list, max_length=MAX_LINKS)
    related_variant_ids: Ids = Field(default_factory=list, max_length=MAX_LINKS)
    images: list[ProductImageDto] = Field(default_factory=list, max_length=50)
    specials: list[ProductSpecialDto] = Field(default_factory=list, max_length=50)
    sizes: list[ProductSizeEditDto] = Field(default_factory=list, max_length=100)
    attributes: list[ProductAttributeDto] = Field(default_factory=list, max_length=100)
    download_ids: Ids = Field(default_factory=list, max_length=MAX_LINKS)


class ProductEditDto(ProductFormDto):
    id: int
    date_modified: datetime


class SaveProductRequest(ProductFormDto):
    """date_modified: the version the form was opened with (PUT only); a newer product is a 409."""

    date_modified: datetime | None = None


class ProductFormLookups(Dto):
    categories: list[LookupItem]
    labels: list[LookupItem]
    filters: list[LookupItem]  # "Група: значення"
    sizes: list[LookupItem]  # values of the size option
    attributes: list[LookupItem]  # "Група: атрибут"
    downloads: list[LookupItem]


class CreatedProductDto(Dto):
    product_id: int


class RemoveNewRequest(Dto):
    sku: str = Field(default="", max_length=64)


class RemoveNewResult(Dto):
    product_ids: list[int]
    label_removed: bool
    filter_removed: bool


class ProductEditStore(Protocol):
    async def get(self, product_id: int) -> ProductEditDto | None: ...

    async def lookups(self) -> ProductFormLookups: ...

    async def existing_products(self, product_ids: set[int]) -> set[int]: ...

    async def seo_keyword_owner(self, keyword: str) -> str | None:
        """The query ("product_id=5", "category_id=2"…) that already uses this address, or None."""
        ...

    async def create(self, form: ProductFormDto, now: datetime) -> int: ...

    async def save(self, product_id: int, form: ProductFormDto, expected: datetime, now: datetime) -> None:
        """Writes everything while date_modified still equals expected; otherwise raises ConflictError."""
        ...

    async def remove_new(self, sku: str) -> RemoveNewResult: ...


class ProductEditService:
    def __init__(self, store: ProductEditStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def get(self, product_id: int) -> ProductEditDto:
        product = await self._store.get(product_id)
        if product is None:
            raise NotFoundError(f"Товар {product_id} не знайдено.")
        return product

    async def lookups(self) -> ProductFormLookups:
        return await self._store.lookups()

    async def create(self, request: SaveProductRequest, user_id: int) -> CreatedProductDto:
        form = await self._checked(request, None)
        product_id = await self._store.create(form, self._now())
        audit.info("product created: product %s, by user %s", product_id, user_id)
        return CreatedProductDto(product_id=product_id)

    async def save(self, product_id: int, request: SaveProductRequest, user_id: int) -> ProductEditDto:
        current = await self.get(product_id)
        if request.date_modified is None:
            raise DomainError("Не вказано dateModified: з якою версією товару ви працювали.")
        form = await self._checked(request, product_id, current)
        await self._store.save(product_id, form, request.date_modified, self._now())
        if current.price != form.price:
            audit.info("product price changed: product %s, %s -> %s, by user %s",
                       product_id, current.price, form.price, user_id)  # fmt: skip
        return await self.get(product_id)

    async def copy(self, product_id: int, user_id: int) -> CreatedProductDto:
        """Like OpenCart's copy, but disabled, with no stock and no SEO address."""
        source = await self.get(product_id)
        sizes = [size.model_copy(update={"quantity": 0}) for size in source.sizes]
        form = ProductFormDto(**source.model_dump(exclude={"id", "date_modified"}))
        form = form.model_copy(update={"enabled": False, "seo_keyword": "", "sizes": sizes})
        new_id = await self._store.create(form, self._now())
        audit.info("product copied: product %s from %s, by user %s", new_id, product_id, user_id)
        return CreatedProductDto(product_id=new_id)

    async def remove_new(self, request: RemoveNewRequest) -> RemoveNewResult:
        sku = request.sku.strip()
        if not sku:
            raise DomainError("Вкажіть код принта (SKU).")
        result = await self._store.remove_new(sku)
        if not result.product_ids:
            raise NotFoundError(f"Товарів з SKU «{sku}» не знайдено.")
        return result

    async def _checked(
        self, request: SaveProductRequest, product_id: int | None, current: ProductEditDto | None = None
    ) -> ProductFormDto:
        # The formats first, so a bad value is a 400 before anything is looked up.
        image = image_path(request.image)
        images = [i.model_copy(update={"image": image_path(i.image)}) for i in request.images if i.image]
        keyword = seo_keyword(request.seo_keyword)
        if not request.name.strip():
            raise DomainError("Вкажіть назву товару.")
        if not request.model.strip():
            raise DomainError("Вкажіть модель товару.")
        lookups = await self._store.lookups()
        # A category, label or filter turned off later may stay in the product, but cannot be chosen again.
        _known("категорії", request.category_ids, lookups.categories, current.category_ids if current else [])
        _known("мітки", request.label_ids, lookups.labels, current.label_ids if current else [])
        _known("фільтри", request.filter_ids, lookups.filters, current.filter_ids if current else [])
        size_ids = [size.option_value_id for size in request.sizes]
        _known("розміри", size_ids, lookups.sizes, [])
        _known("атрибути", [a.attribute_id for a in request.attributes], lookups.attributes, [])
        _known("завантаження", request.download_ids, lookups.downloads, current.download_ids if current else [])
        if len(set(size_ids)) != len(size_ids):
            raise DomainError("Розмір у списку двічі.")
        if current is not None:
            kept = set(size_ids)
            for size in current.sizes:
                if size.option_value_id not in kept and size.quantity > 0:
                    raise DomainError(f"Розмір {size.name} не можна прибрати: на вітрині {size.quantity} шт.")
        related = set(request.related_ids) | set(request.related_color_ids) | set(request.related_variant_ids)
        related.discard(product_id or 0)
        missing = related - await self._store.existing_products(related)
        if missing:
            raise DomainError(f"Пов'язаних товарів немає: {', '.join(map(str, sorted(missing)))}.")
        for special in request.specials:
            if special.date_start and special.date_end and special.date_end < special.date_start:
                raise DomainError("Акційна ціна: кінець раніше за початок.")
        if keyword:
            owner = await self._store.seo_keyword_owner(keyword)
            if owner is not None and owner != f"product_id={product_id}":
                raise ConflictError(f"SEO-адресу «{keyword}» вже має {owner}.")
        html = {name: clean_html(getattr(request, name)) for name in HTML_FIELDS}
        return ProductFormDto(
            **request.model_dump(exclude={"date_modified", *HTML_FIELDS}),
            **html,
        ).model_copy(
            update={
                "name": request.name.strip(),
                "model": request.model.strip(),
                "image": image,
                "images": images,
                "seo_keyword": keyword,
                "related_ids": _unique(request.related_ids, product_id),
                "related_color_ids": _unique(request.related_color_ids, product_id),
                "related_variant_ids": _unique(request.related_variant_ids, product_id),
                "attributes": list({a.attribute_id: a for a in request.attributes if a.text.strip()}.values()),
                "download_ids": list(dict.fromkeys(request.download_ids)),
            }
        )

    def _now(self) -> datetime:
        # date_modified is a datetime without fractions of a second, as OpenCart writes it.
        return self._clock.now().replace(microsecond=0)


HTML_FIELDS = ("description", "table_size", "notice", "model_info", "characteristics")


def _known(what: str, chosen: list[int], allowed: list[LookupItem], kept: list[int]) -> None:
    unknown = set(chosen) - {item.id for item in allowed} - set(kept)
    if unknown:
        raise DomainError(f"Невідомі {what}: {', '.join(map(str, sorted(unknown)))}.")


def _unique(ids: list[int], own_id: int | None) -> list[int]:
    """In their order, once each, without the product itself."""
    return [i for i in dict.fromkeys(ids) if i != own_id]
