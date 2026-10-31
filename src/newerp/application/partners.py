"""Reseller partners and their XML feed (yml_catalog), which the partner's site downloads."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from xml.sax.saxutils import escape, quoteattr

from newerp.application.common import Clock, Dto, Money, NotFoundError
from newerp.domain.common import DomainError, plain
from newerp.domain.partners import (
    CategoryMapping,
    FeedProduct,
    Partner,
    PartnerFeed,
    PartnerSettings,
    ProductMapping,
    build_feed,
)

SEARCH_LIMIT = 20


@dataclass(frozen=True)
class PartnerFeedOptions:
    """The shop in the feed header (section PartnerFeed in .NET)."""

    shop_name: str = "Vidro"
    company: str = ""
    store_url: str = ""


class PartnerListItemDto(Dto):
    id: int
    code: str
    name: str
    vendor_name: str
    feed_enabled: bool
    is_active: bool
    categories: int
    products: int


class PartnerCategoryMapDto(Dto):
    id: int
    category_id: int
    category_name: str
    size_name: str | None
    external_id: str
    external_name: str
    percent_supply: Money | None
    percent_sale: Money | None


class PartnerProductMapDto(Dto):
    id: int
    product_id: int
    product_name: str
    product_enabled: bool
    size_name: str | None
    external_id: str
    external_name: str
    price_supply: Money | None
    price_sale: Money | None


class PartnerDto(Dto):
    id: int
    code: str
    name: str
    is_active: bool
    feed_url: str
    feed_enabled: bool
    vendor_name: str
    notes: str
    default_quantity: int
    date_modified: datetime
    category_map: list[PartnerCategoryMapDto]
    product_map: list[PartnerProductMapDto]


class CategoryMapRow(Dto):
    category_id: int
    size_name: str | None = None
    external_id: str | None = ""
    external_name: str | None = ""
    percent_supply: Decimal | None = None
    percent_sale: Decimal | None = None


class ProductMapRow(Dto):
    product_id: int
    size_name: str | None = None
    external_id: str | None = ""
    external_name: str | None = ""
    price_supply: Decimal | None = None
    price_sale: Decimal | None = None


class SavePartnerRequest(Dto):
    code: str | None = None
    name: str | None = None
    is_active: bool = True
    feed_url: str | None = ""
    feed_enabled: bool = False
    vendor_name: str | None = "Vidro"
    notes: str | None = ""
    default_quantity: int = 5
    category_map: list[CategoryMapRow] | None = None
    product_map: list[ProductMapRow] | None = None


class ShopCategoryDto(Dto):
    id: int
    name: str
    sizes: list[str]


class ProductSizePriceDto(Dto):
    name: str
    price: Money


class PartnerProductPickDto(Dto):
    id: int
    name: str
    is_enabled: bool
    sizes: list[ProductSizePriceDto]


class PartnerRepository(Protocol):
    async def list_all(self) -> list[PartnerListItemDto]: ...

    async def get_dto(self, partner_id: int) -> PartnerDto | None: ...

    async def get(self, partner_id: int) -> Partner | None: ...

    async def get_by_code(self, code: str) -> Partner | None: ...

    async def code_exists(self, code: str, except_id: int | None) -> bool: ...

    def add(self, partner: Partner) -> None: ...

    async def delete(self, partner: Partner) -> None: ...

    async def save_changes(self) -> None: ...

    async def list_categories(self) -> list[ShopCategoryDto]: ...

    async def search_products(self, term: str, limit: int) -> list[PartnerProductPickDto]: ...

    async def get_feed_products(self, category_ids: set[int], product_ids: set[int]) -> list[FeedProduct]: ...

    async def get_category_names(self, category_ids: set[int]) -> dict[int, str]: ...


class PartnerService:
    def __init__(self, repository: PartnerRepository, options: PartnerFeedOptions, clock: Clock) -> None:
        self._repository = repository
        self._options = options
        self._clock = clock

    async def create(self, request: SavePartnerRequest) -> int:
        partner = Partner(_to_settings(request), self._clock.now())
        _apply(partner, request)
        await self._ensure_unique_code(partner.code, None)
        self._repository.add(partner)
        await self._repository.save_changes()
        assert partner.id is not None
        return partner.id

    async def update(self, partner_id: int, request: SavePartnerRequest) -> None:
        partner = await self._get(partner_id)
        partner.update(_to_settings(request), self._clock.now())
        _apply(partner, request)
        await self._ensure_unique_code(partner.code, partner_id)
        await self._repository.save_changes()

    async def delete(self, partner_id: int) -> None:
        await self._repository.delete(await self._get(partner_id))
        await self._repository.save_changes()

    async def search_products(self, term: str | None) -> list[PartnerProductPickDto]:
        if not term or not term.strip():
            return []
        return await self._repository.search_products(term.strip(), SEARCH_LIMIT)

    async def build_feed_xml(self, code: str) -> str | None:
        """None when there is no such partner or its feed is off (the API answers 404)."""
        partner = await self._repository.get_by_code(code)
        if partner is None or not partner.feed_enabled:
            return None
        products = await self._repository.get_feed_products(
            {r.category_id for r in partner.category_map}, {r.product_id for r in partner.product_map}
        )
        category_ids = {r.category_id for r in partner.category_map} | {c for p in products for c in p.category_ids}
        category_names = await self._repository.get_category_names(category_ids)
        store_url = self._options.store_url.strip()
        if store_url and not store_url.endswith("/"):
            store_url += "/"
        feed = build_feed(partner, products, category_names, store_url)
        return _write_xml(feed, partner, self._options, store_url, self._clock.now())

    async def _get(self, partner_id: int) -> Partner:
        partner = await self._repository.get(partner_id)
        if partner is None:
            raise NotFoundError(f"Партнера {partner_id} не знайдено.")
        return partner

    async def _ensure_unique_code(self, code: str, except_id: int | None) -> None:
        if await self._repository.code_exists(code, except_id):
            raise DomainError(f"Партнер з кодом «{code}» уже є.")


def _to_settings(r: SavePartnerRequest) -> PartnerSettings:
    return PartnerSettings(
        r.code, r.name, r.is_active, r.feed_url, r.feed_enabled, r.vendor_name, r.notes, r.default_quantity
    )


def _apply(partner: Partner, request: SavePartnerRequest) -> None:
    partner.set_category_map(
        CategoryMapping(r.category_id, r.size_name, r.external_id, r.external_name, r.percent_supply, r.percent_sale)
        for r in request.category_map or []
    )
    partner.set_product_map(
        ProductMapping(r.product_id, r.size_name, r.external_id, r.external_name, r.price_supply, r.price_sale)
        for r in request.product_map or []
    )


def _write_xml(feed: PartnerFeed, partner: Partner, shop: PartnerFeedOptions, store_url: str, now: datetime) -> str:
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f"<yml_catalog date={quoteattr(f'{now:%Y-%m-%d %H:%M}')}>",
        "  <shop>",
        _element(2, "name", shop.shop_name),
        _element(2, "company", shop.company),
        _element(2, "url", store_url),
        "    <currencies>",
        '      <currency id="UAH" rate="1" />',
        "    </currencies>",
        "    <categories>",
    ]
    for category in feed.categories:
        # The attribute is named after the first partner (EVA); its feed already reads this name.
        eva_id = f" eva_id={quoteattr(category.external_id)}" if category.external_id is not None else ""
        lines.append(f"      <category id={quoteattr(category.id)}{eva_id}>{escape(category.name)}</category>")
    lines += ["    </categories>", "    <offers>"]
    for offer in feed.offers:
        lines.append(f'      <offer id={quoteattr(offer.id)} available="true">')
        lines.append(_element(4, "price", plain(offer.price)))
        if offer.price_old is not None:
            lines.append(_element(4, "price_old", plain(offer.price_old)))
        lines.append(_element(4, "stock_quantity", str(partner.default_quantity)))
        lines.append(_element(4, "currencyId", "UAH"))
        lines.append(_element(4, "categoryId", offer.category_id))
        lines += [_element(4, "picture", picture) for picture in offer.pictures]
        lines.append(_element(4, "vendor", partner.vendor_name))
        if offer.model:
            lines.append(_element(4, "article", offer.model))
        if offer.vendor_code is not None:
            lines.append(_element(4, "vendorCode", offer.vendor_code))
        lines.append(_element(4, "name_ua", offer.name))
        # CDATA cannot hold "]]>", so it is split across two sections, as .NET XmlWriter does.
        description = offer.description.replace("]]>", "]]]]><![CDATA[>")
        lines.append(f"        <description_ua><![CDATA[{description}]]></description_ua>")
        if offer.size is not None:
            lines.append(f'        <param name="Розмір">{escape(offer.size)}</param>')
        lines.append(f'        <param name="Штрихкод">{escape(offer.barcode)}</param>')
        lines.append("      </offer>")
    lines += ["    </offers>", "  </shop>", "</yml_catalog>"]
    return "\n".join(lines)


def _element(depth: int, name: str, text: str) -> str:
    return f"{'  ' * depth}<{name}>{escape(text)}</{name}>"
