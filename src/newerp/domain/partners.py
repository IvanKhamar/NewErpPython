"""Reseller partners of the old site (dd_partner) and their XML feed, built like the old eva.php.

A partner maps shop categories (optionally one size) to its own categories with a markup in percent, and can also list
single products with fixed prices.
"""

import html
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote

from newerp.domain.common import DomainError, round_money

CODE_MAX_LENGTH = 32
NAME_MAX_LENGTH = 128
_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class PartnerSettings:
    code: str | None
    name: str | None
    is_active: bool = True
    feed_url: str | None = ""
    feed_enabled: bool = False
    vendor_name: str | None = ""
    notes: str | None = ""
    default_quantity: int = 5


@dataclass(frozen=True)
class CategoryMapping:
    category_id: int
    size_name: str | None = None
    external_id: str | None = ""
    external_name: str | None = ""
    percent_supply: Decimal | None = None
    percent_sale: Decimal | None = None


@dataclass(frozen=True)
class ProductMapping:
    product_id: int
    size_name: str | None = None
    external_id: str | None = ""
    external_name: str | None = ""
    price_supply: Decimal | None = None
    price_sale: Decimal | None = None


class PartnerCategoryMap:
    """A shop category (empty size = all sizes) → the partner's category, with markups to the price and old price."""

    def __init__(self, category_id: int, size_name: str | None) -> None:
        self.id: int | None = None
        self.category_id = category_id
        self.size_name = size_name
        self.external_id = ""
        self.external_name = ""
        self.percent_supply: Decimal | None = None
        self.percent_sale: Decimal | None = None

    def apply(self, mapping: CategoryMapping) -> None:
        self.external_id = mapping.external_id or ""
        self.external_name = mapping.external_name or ""
        self.percent_supply = mapping.percent_supply
        self.percent_sale = mapping.percent_sale


class PartnerProductMap:
    """A single product (empty size = all sizes) in the feed with the partner's article, name and fixed prices."""

    def __init__(self, product_id: int, size_name: str | None) -> None:
        self.id: int | None = None
        self.product_id = product_id
        self.size_name = size_name
        self.external_id = ""
        self.external_name = ""
        self.price_supply: Decimal | None = None
        self.price_sale: Decimal | None = None

    def apply(self, mapping: ProductMapping) -> None:
        self.external_id = mapping.external_id or ""
        self.external_name = mapping.external_name or ""
        self.price_supply = mapping.price_supply
        self.price_sale = mapping.price_sale


class Partner:
    def __init__(self, settings: PartnerSettings, now: datetime) -> None:
        self.id: int | None = None
        self._category_map: list[PartnerCategoryMap] = []
        self._product_map: list[PartnerProductMap] = []
        self.update(settings, now)
        self.date_added = now

    @property
    def category_map(self) -> list[PartnerCategoryMap]:
        return list(self._category_map)

    @property
    def product_map(self) -> list[PartnerProductMap]:
        return list(self._product_map)

    def update(self, settings: PartnerSettings, now: datetime) -> None:
        code = (settings.code or "").strip()
        if not 0 < len(code) <= CODE_MAX_LENGTH or not _CODE_PATTERN.match(code):
            raise DomainError(f"Код партнера — від 1 до {CODE_MAX_LENGTH} латинських літер, цифр, «-» чи «_».")
        name = (settings.name or "").strip()
        if not 0 < len(name) <= NAME_MAX_LENGTH:
            raise DomainError(f"Назва партнера — від 1 до {NAME_MAX_LENGTH} символів.")
        if settings.default_quantity < 0:
            raise DomainError("Кількість у фіді не може бути від'ємною.")
        self.code = code
        self.name = name
        self.is_active = settings.is_active
        self.feed_url = _limit(settings.feed_url, 255, "Адреса фіда")
        self.feed_enabled = settings.feed_enabled
        self.vendor_name = _limit(settings.vendor_name, 64, "Бренд")
        self.notes = (settings.notes or "").strip()
        self.default_quantity = settings.default_quantity
        self.date_modified = now

    def set_category_map(self, rows: Iterable[CategoryMapping]) -> None:
        mappings = [
            replace(
                r,
                size_name=_normalize_size(r.size_name),
                external_id=_limit(r.external_id, 64, "Id категорії партнера"),
                external_name=_limit(r.external_name, 255, "Назва категорії партнера"),
            )
            for r in rows
        ]
        for m in mappings:
            if m.category_id <= 0:
                raise DomainError("Оберіть категорію в кожному рядку відповідності.")
            _check_percent(m.percent_supply)
            _check_percent(m.percent_sale)
        _check_unique([(m.category_id, m.size_name) for m in mappings], "Категорія з таким розміром")
        _merge(
            self._category_map,
            mappings,
            lambda row, m: (row.category_id, row.size_name) == (m.category_id, m.size_name),
            lambda m: PartnerCategoryMap(m.category_id, m.size_name),
        )

    def set_product_map(self, rows: Iterable[ProductMapping]) -> None:
        mappings = [
            replace(
                r,
                size_name=_normalize_size(r.size_name),
                external_id=_limit(r.external_id, 64, "Артикул партнера"),
                external_name=_limit(r.external_name, 255, "Назва в партнера"),
            )
            for r in rows
        ]
        for m in mappings:
            if m.product_id <= 0:
                raise DomainError("Оберіть товар у кожному рядку.")
            if any(price is not None and price <= 0 for price in (m.price_supply, m.price_sale)):
                raise DomainError("Ціна для партнера має бути більшою за нуль.")
        _check_unique([(m.product_id, m.size_name) for m in mappings], "Товар з таким розміром")
        _merge(
            self._product_map,
            mappings,
            lambda row, m: (row.product_id, row.size_name) == (m.product_id, m.size_name),
            lambda m: PartnerProductMap(m.product_id, m.size_name),
        )


def _merge(rows: list, mappings: list, same: Callable, create: Callable) -> None:
    """Rows with the same key keep their id (the feed uses it as the category id); the rest are removed or added."""
    for row in [row for row in rows if not any(same(row, m) for m in mappings)]:
        rows.remove(row)
    for mapping in mappings:
        row = next((r for r in rows if same(r, mapping)), None)
        if row is None:
            row = create(mapping)
            rows.append(row)
        row.apply(mapping)


def _check_unique(keys: list[tuple[int, str | None]], what: str) -> None:
    seen: set[tuple[int, str | None]] = set()
    for key in keys:
        if key in seen:
            size = "" if key[1] is None else f", {key[1]}"
            raise DomainError(f"{what} ({key[0]}{size}) вказано двічі.")
        seen.add(key)


def _check_percent(percent: Decimal | None) -> None:
    if percent is not None and percent <= -100:
        raise DomainError("Відсоток не може зменшувати ціну до нуля чи нижче (має бути більшим за −100).")


def _normalize_size(size: str | None) -> str | None:
    return size.strip() if size and size.strip() else None


def _limit(value: str | None, max_length: int, field: str) -> str:
    trimmed = (value or "").strip()
    if len(trimmed) > max_length:
        raise DomainError(f"{field} — не довше {max_length} символів.")
    return trimmed


# ═══ Feed ═══


@dataclass(frozen=True)
class FeedProductOption:
    option_value_id: int
    name: str
    price: Decimal


@dataclass(frozen=True)
class FeedProduct:
    product_id: int
    model: str
    sku: str
    price: Decimal
    name: str
    description: str
    category_ids: Sequence[int]
    images: Sequence[str]
    options: Sequence[FeedProductOption]


@dataclass(frozen=True)
class FeedCategory:
    id: str
    external_id: str | None
    name: str


@dataclass(frozen=True)
class FeedOffer:
    id: str
    price: Decimal
    price_old: Decimal | None
    category_id: str
    pictures: list[str]
    model: str
    vendor_code: str | None
    name: str
    description: str
    size: str | None
    barcode: str


@dataclass(frozen=True)
class PartnerFeed:
    categories: list[FeedCategory]
    offers: list[FeedOffer]


def build_feed(
    partner: Partner, products: Sequence[FeedProduct], category_names: Mapping[int, str], store_url: str
) -> PartnerFeed:
    # Rows not saved yet have no id; sorted() is stable, so they keep their order.
    category_rows = sorted(partner.category_map, key=lambda r: r.id or 0)
    # Key "category|size"; an empty size is the whole category. As in the old code, a later row overrides one before.
    map_index = {_key(row.category_id, row.size_name): row for row in category_rows}
    mapped_category_ids = {row.category_id for row in category_rows}
    products_by_id = {p.product_id: p for p in products}

    standalone: dict[str, FeedOffer] = {}
    extra_categories: dict[int, str] = {}
    for row in sorted(partner.product_map, key=lambda r: r.id or 0):
        product = products_by_id.get(row.product_id)
        if product is None:
            continue
        category_id = min(product.category_ids, default=0)
        category_row = _resolve(map_index, category_id, row.size_name)
        if category_row is not None:
            feed_category_id = str(category_row.id or 0)
        else:
            feed_category_id = f"p{category_id}"
            extra_categories[category_id] = category_names.get(category_id, "")
        if product.options:
            for variant, option in enumerate(product.options):
                if row.size_name is not None and option.name.strip() != row.size_name:
                    continue
                offer_id = f"p{product.product_id}_{option.option_value_id}"
                name = row.external_name or f"{product.name} - {option.name}".strip()
                price = row.price_supply if row.price_supply is not None else option.price
                standalone[offer_id] = _offer(
                    offer_id, product, option, price, row.price_sale, feed_category_id, name, row.external_id, variant,
                    store_url,
                )  # fmt: skip
        else:
            offer_id = f"p{product.product_id}"
            price = row.price_supply if row.price_supply is not None else product.price
            standalone[offer_id] = _offer(
                offer_id, product, None, price, row.price_sale, feed_category_id, row.external_name or product.name,
                row.external_id, 0, store_url,
            )  # fmt: skip

    # A product added as its own row is not repeated by an offer from its category (the old feed gave both).
    offers: list[FeedOffer] = []
    for product in sorted(products, key=lambda p: p.product_id):
        category_ids = [c for c in product.category_ids if c in mapped_category_ids]
        if not category_ids:
            continue
        category_id = min(category_ids)
        if product.options:
            for variant, option in enumerate(product.options):
                category_row = _resolve(map_index, category_id, option.name)
                if category_row is None or f"p{product.product_id}_{option.option_value_id}" in standalone:
                    continue
                offers.append(
                    _category_offer(
                        f"{product.product_id}_{option.option_value_id}", product, option, option.price, category_row,
                        f"{product.name} - {option.name}".strip(), variant, store_url,
                    )
                )  # fmt: skip
        else:
            category_row = _resolve(map_index, category_id, None)
            if category_row is None or f"p{product.product_id}" in standalone:
                continue
            offers.append(
                _category_offer(
                    str(product.product_id), product, None, product.price, category_row, product.name, 0, store_url
                )
            )
    offers.extend(standalone.values())

    categories = [
        FeedCategory(str(r.id or 0), r.external_id or None, r.external_name or category_names.get(r.category_id, ""))
        for r in category_rows
    ]
    categories += [FeedCategory(f"p{key}", None, name) for key, name in sorted(extra_categories.items())]
    # The partner does not take offers without pictures, so they are skipped, as in the old feed.
    return PartnerFeed(categories, [o for o in offers if o.pictures])


def barcode(sku: str, product_id: int, variant: int) -> str:
    """The old site's EAN-13: 482 + 4 digits of the SKU + 4 digits of the product id + variant digit + check digit."""
    sku_part = sku[:4].rjust(4, "0") if sku and all(c in "0123456789" for c in sku) else "0000"
    id_part = str(product_id)[:4].rjust(4, "0")
    payload = f"482{sku_part}{id_part}{variant % 10}"
    total = sum(int(digit) * (1 if i % 2 == 0 else 3) for i, digit in enumerate(payload))
    return payload + str((10 - total % 10) % 10)


_LINE_BREAK_TAGS = re.compile(r"</p>|<br\s*/?>", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]*>")


def plain_description(text: str) -> str:
    # OpenCart stores the description with escaped entities (&lt;p&gt;), so it is decoded twice, like the old feed.
    text = html.unescape(html.unescape(text))
    text = _LINE_BREAK_TAGS.sub("\n", text)
    return _TAGS.sub("", text).strip()


def _category_offer(
    offer_id: str,
    product: FeedProduct,
    option: FeedProductOption | None,
    base_price: Decimal,
    category_row: PartnerCategoryMap,
    name: str,
    variant: int,
    store_url: str,
) -> FeedOffer:
    price = base_price * (1 + (category_row.percent_supply or 0) / Decimal(100))
    sale = category_row.percent_sale
    price_old = base_price * (1 + sale / Decimal(100)) if sale is not None else None
    return _offer(
        offer_id, product, option, price, price_old, str(category_row.id or 0), name, None, variant, store_url
    )


def _offer(
    offer_id: str,
    product: FeedProduct,
    option: FeedProductOption | None,
    price: Decimal,
    price_old: Decimal | None,
    category_id: str,
    name: str,
    vendor_code: str | None,
    variant: int,
    store_url: str,
) -> FeedOffer:
    return FeedOffer(
        id=offer_id,
        price=round_money(price),
        price_old=round_money(price_old) if price_old is not None else None,
        category_id=category_id,
        pictures=[_picture_url(store_url, image) for image in product.images if image],
        model=product.model,
        vendor_code=vendor_code or None,
        name=name,
        description=plain_description(product.description),
        size=option.name if option is not None and option.name else None,
        barcode=barcode(product.sku, product.product_id, variant),
    )


def _resolve(
    map_index: dict[str, PartnerCategoryMap], category_id: int, size_name: str | None
) -> PartnerCategoryMap | None:
    if size_name is not None and (by_size := map_index.get(_key(category_id, size_name))) is not None:
        return by_size
    return map_index.get(_key(category_id, None))


def _key(category_id: int, size_name: str | None) -> str:
    return f"{category_id}|{(size_name or '').strip()}"


def _picture_url(store_url: str, path: str) -> str:
    return store_url + "image/" + "/".join(quote(segment, safe="") for segment in path.split("/"))
