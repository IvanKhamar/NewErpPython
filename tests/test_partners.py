"""Partners and the XML feed. Its own database: the test adds pictures and a category name to the catalog."""

import asyncio
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from newerp.application.partners import PartnerFeedOptions, PartnerService, SavePartnerRequest
from newerp.domain.common import DomainError
from newerp.domain.partners import (
    CategoryMapping,
    FeedCategory,
    FeedProduct,
    FeedProductOption,
    Partner,
    PartnerSettings,
    ProductMapping,
    barcode,
    build_feed,
    plain_description,
)
from tests.helpers import create
from tests.seed import (
    CATEGORY_ID,
    CATEGORY_PRODUCT_ID,
    LANGUAGE_ID,
    MANAGER,
    SIZE_M_OPTION_VALUE_ID,
    STOREKEEPER,
    insert,
)

NOW = datetime(2026, 10, 18, 12, 0)
STORE = "https://shop.test/"
CATEGORY_NAMES = {5: "Сорочки", 6: "Сукні", 9: "Аксесуари"}


def new_partner(code: str = "eva", default_quantity: int = 5) -> Partner:
    return Partner(PartnerSettings(code, "EVA", True, "", True, "Vidro", "", default_quantity), NOW)


def product(product_id: int, category: int, *options: FeedProductOption) -> FeedProduct:
    return FeedProduct(
        product_id, f"T-{product_id}", "", Decimal(1000), f"Товар {product_id}", "<p>Льон</p>", [category],
        ["catalog/фото 1.jpg"], options,
    )  # fmt: skip


def size(option_value_id: int, name: str, price: int) -> FeedProductOption:
    return FeedProductOption(option_value_id, name, Decimal(price))


# ═══ Partner ═══


@pytest.mark.parametrize("code", ["", "ева", "eva feed", "a23456789012345678901234567890123"])
def test_code_must_be_short_latin(code):
    with pytest.raises(DomainError):
        new_partner(code)


def test_negative_quantity_is_rejected():
    with pytest.raises(DomainError):
        new_partner(default_quantity=-1)


def test_set_category_map_keeps_existing_rows_so_feed_category_ids_stay_the_same():
    partner = new_partner()
    partner.set_category_map([CategoryMapping(5, None, "100", "", Decimal(10)), CategoryMapping(5, "M")])
    whole_category = partner.category_map[0]

    partner.set_category_map([CategoryMapping(5, " ", "200", "Сорочки", Decimal(15), Decimal(30)), CategoryMapping(6)])

    assert len(partner.category_map) == 2
    assert partner.category_map[0] is whole_category  # an empty size means all sizes: the same row
    assert (whole_category.external_id, whole_category.external_name) == ("200", "Сорочки")
    assert (whole_category.percent_supply, whole_category.percent_sale) == (15, 30)
    assert partner.category_map[1].category_id == 6


@pytest.mark.parametrize(
    "rows",
    [
        [CategoryMapping(5, "M"), CategoryMapping(5, "M ")],
        [CategoryMapping(5, None, "", "", Decimal(-100))],
        [CategoryMapping(0)],
    ],
)
def test_set_category_map_rejects_duplicates_and_bad_percents(rows):
    with pytest.raises(DomainError):
        new_partner().set_category_map(rows)


def test_set_product_map_rejects_zero_price():
    with pytest.raises(DomainError):
        new_partner().set_product_map([ProductMapping(10, None, "", "", Decimal(0))])


# ═══ Feed builder ═══


def test_category_offers_have_markup_and_old_price_per_size():
    partner = new_partner()
    partner.set_category_map([CategoryMapping(5, None, "77", "", Decimal(10), Decimal(25))])

    feed = build_feed(partner, [product(12, 5, size(46, "S", 900), size(47, "M", 1000))], CATEGORY_NAMES, STORE)

    assert [o.id for o in feed.offers] == ["12_46", "12_47"]
    m = feed.offers[1]
    assert (m.price, m.price_old) == (1100, 1250)
    assert (m.name, m.size, m.description) == ("Товар 12 - M", "M", "Льон")
    assert m.barcode == barcode("", 12, 1)  # the second size: variant digit 1
    assert m.pictures == ["https://shop.test/image/catalog/%D1%84%D0%BE%D1%82%D0%BE%201.jpg"]
    assert feed.categories == [FeedCategory("0", "77", "Сорочки")]


def test_size_row_wins_over_whole_category_and_unmapped_sizes_are_skipped():
    partner = new_partner()
    partner.set_category_map([CategoryMapping(5, "M", "", "", Decimal(50)), CategoryMapping(6, "L")])

    feed = build_feed(
        partner,
        [product(12, 5, size(46, "S", 900), size(47, "M", 1000)), product(13, 6, size(47, "M", 1000))],
        CATEGORY_NAMES,
        STORE,
    )

    [offer] = feed.offers
    assert (offer.id, offer.price, offer.price_old) == ("12_47", 1500, None)


def test_product_row_replaces_category_offer_and_adds_own_category_when_unmapped():
    partner = new_partner()
    partner.set_category_map([CategoryMapping(5)])
    partner.set_product_map(
        [
            ProductMapping(12, "M", "EVA-12", "Сорочка для EVA", Decimal(1300), Decimal(1600)),
            ProductMapping(20),
        ]
    )
    no_sizes = replace(product(20, 9), price=Decimal(450))

    feed = build_feed(
        partner, [product(12, 5, size(46, "S", 900), size(47, "M", 1000)), no_sizes], CATEGORY_NAMES, STORE
    )

    assert [o.id for o in feed.offers] == ["12_46", "p12_47", "p20"]
    mapped = feed.offers[1]
    assert (mapped.price, mapped.price_old, mapped.vendor_code, mapped.name, mapped.category_id) == (
        1300, 1600, "EVA-12", "Сорочка для EVA", "0",
    )  # fmt: skip
    assert (feed.offers[2].category_id, feed.offers[2].price, feed.offers[2].name) == ("p9", 450, "Товар 20")
    assert FeedCategory("p9", None, "Аксесуари") in feed.categories


def test_offers_without_pictures_are_skipped():
    partner = new_partner()
    partner.set_category_map([CategoryMapping(5)])

    feed = build_feed(partner, [replace(product(12, 5), images=[])], CATEGORY_NAMES, STORE)

    assert feed.offers == []


@pytest.mark.parametrize(
    ("sku", "product_id", "variant", "expected"),
    [("123", 12345, 2, "4820123123426"), ("", 102, 0, "4820000010207"), ("AB1", 7, 11, "4820000000710")],
)
def test_barcode_is_the_same_ean13_as_on_the_old_site(sku, product_id, variant, expected):
    assert barcode(sku, product_id, variant) == expected


def test_plain_description_decodes_html_and_keeps_line_breaks():
    text = "&lt;p&gt;Льон 100%&lt;/p&gt;&lt;b&gt;Прати&lt;/b&gt; при 30&amp;deg;"
    assert plain_description(text) == "Льон 100%\nПрати при 30°"


# ═══ Service ═══


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 18, 9, 30)


class FakePartnerRepository:
    def __init__(self) -> None:
        self.partners: list[Partner] = []
        self.products: list[FeedProduct] = []

    async def get_by_code(self, code):
        return next((p for p in self.partners if p.code == code), None)

    async def code_exists(self, code, except_id):
        return any(p.code == code and p.id != except_id for p in self.partners)

    def add(self, partner):
        partner.id = len(self.partners) + 1
        self.partners.append(partner)

    async def save_changes(self):
        pass

    async def get_feed_products(self, category_ids, product_ids):
        return self.products

    async def get_category_names(self, category_ids):
        return {5: "Сорочки"}


def service(repository: FakePartnerRepository) -> PartnerService:
    options = PartnerFeedOptions(shop_name="Vidro", company="ФОП Тест", store_url="https://shop.test")
    return PartnerService(repository, options, FixedClock())


def test_create_rejects_taken_code():
    partners = service(FakePartnerRepository())
    asyncio.run(partners.create(SavePartnerRequest(code="eva", name="EVA")))

    with pytest.raises(DomainError):
        asyncio.run(partners.create(SavePartnerRequest(code="eva", name="Інший")))


def test_feed_is_none_when_disabled_or_unknown():
    partners = service(FakePartnerRepository())
    asyncio.run(partners.create(SavePartnerRequest(code="eva", name="EVA", feed_enabled=False)))

    assert asyncio.run(partners.build_feed_xml("eva")) is None
    assert asyncio.run(partners.build_feed_xml("rozetka")) is None


def test_feed_is_yml_catalog_with_offers():
    repository = FakePartnerRepository()
    partners = service(repository)
    request = {
        "code": "eva", "name": "EVA", "feedEnabled": True, "vendorName": "Vidro", "defaultQuantity": 7,
        "categoryMap": [{"categoryId": 5, "externalId": "77", "percentSupply": 10}],
    }  # fmt: skip
    asyncio.run(partners.create(SavePartnerRequest.model_validate(request)))
    repository.products.append(
        FeedProduct(
            12,
            "T-12",
            "",
            Decimal(1000),
            "Сорочка",
            "<p>Льон & бавовна</p>",
            [5],
            ["catalog/1.jpg"],
            [size(47, "M", 1000)],
        )  # fmt: skip
    )

    root = ET.fromstring(asyncio.run(partners.build_feed_xml("eva")))

    shop = root.find("shop")
    assert root.get("date") == "2026-10-18 09:30"
    assert (shop.findtext("name"), shop.findtext("company"), shop.findtext("url")) == (
        "Vidro", "ФОП Тест", "https://shop.test/",
    )  # fmt: skip
    category = shop.find("categories/category")
    assert (category.get("eva_id"), category.text) == ("77", "Сорочки")
    offer = shop.find("offers/offer")
    assert offer.get("id") == "12_47"
    assert [offer.findtext(n) for n in ("price", "stock_quantity", "vendor", "article")] == [
        "1100",
        "7",
        "Vidro",
        "T-12",
    ]
    assert offer.find("price_old") is None
    assert offer.findtext("picture") == "https://shop.test/image/catalog/1.jpg"
    assert offer.findtext("description_ua") == "Льон & бавовна"
    assert [p.text for p in offer.findall("param")] == ["M", barcode("", 12, 0)]


# ═══ API ═══


def add_catalog_data(db) -> None:
    """The size M price, main and extra pictures, the category name: what the shared test data does not have."""
    with db.cursor() as cursor:
        cursor.execute("UPDATE dd_product_option_value SET price = 900 WHERE product_id = %s", (CATEGORY_PRODUCT_ID,))
        cursor.execute("UPDATE dd_product SET image = 'catalog/main.jpg' WHERE product_id = %s", (CATEGORY_PRODUCT_ID,))
    insert(db, "dd_product_image", product_id=CATEGORY_PRODUCT_ID, image="catalog/зад.jpg", sort_order=1)
    insert(
        db, "dd_category_description", category_id=CATEGORY_ID, language_id=LANGUAGE_ID, name="Сорочки",
        description="", meta_title="", meta_description="", meta_keyword="",
    )  # fmt: skip


def test_feed_lists_category_products_with_markup(api, db):
    add_catalog_data(db)
    manager = api(MANAGER)
    request = {
        "code": "eva", "name": "EVA", "feedEnabled": True, "defaultQuantity": 3,
        "categoryMap": [{"categoryId": CATEGORY_ID, "externalId": "77", "percentSupply": 10, "percentSale": 20}],
    }  # fmt: skip
    partner_id = create(manager, "/partners", request)

    [category_row] = manager.get(f"/partners/{partner_id}").json()["categoryMap"]
    assert category_row["categoryName"] == "Сорочки"
    categories = manager.get("/partners/categories").json()
    assert next(c for c in categories if c["id"] == CATEGORY_ID)["sizes"] == ["M"]

    # The feed opens without a login, as the partner's site downloads it.
    response = api().get("/feeds/eva.xml")
    assert response.headers["content-type"].startswith("application/xml")
    [offer] = ET.fromstring(response.content).iter("offer")
    assert offer.get("id") == f"{CATEGORY_PRODUCT_ID}_{SIZE_M_OPTION_VALUE_ID}"
    assert [offer.findtext(n) for n in ("price", "price_old", "stock_quantity")] == ["990", "1080", "3"]
    assert offer.findtext("categoryId") == str(category_row["id"])
    assert [p.text for p in offer.findall("picture")] == [
        "image/catalog/main.jpg",
        "image/catalog/%D0%B7%D0%B0%D0%B4.jpg",
    ]
    assert offer.findtext("name_ua") == "Сорочка лляна оверсайз - M"

    # Changing the mapping keeps the category id in the feed; a disabled feed is 404.
    changed = {**request, "feedEnabled": False, "categoryMap": [{**request["categoryMap"][0], "percentSupply": 15}]}
    assert manager.put(f"/partners/{partner_id}", json=changed).status_code == 204
    assert manager.get(f"/partners/{partner_id}").json()["categoryMap"][0]["id"] == category_row["id"]
    assert api().get("/feeds/eva.xml").status_code == 404
    assert manager.post("/partners", json=request).status_code == 400  # the code is taken

    assert api(STOREKEEPER).get("/partners").status_code == 403
    assert manager.delete(f"/partners/{partner_id}").status_code == 204
    assert manager.get("/partners").json() == []
    assert manager.get(f"/partners/{partner_id}").status_code == 404


def test_product_search_lists_size_prices(api):
    [found] = api(MANAGER).get("/partners/products", params={"search": "лляна"}).json()

    assert (found["id"], found["isEnabled"]) == (CATEGORY_PRODUCT_ID, True)
    assert [s["name"] for s in found["sizes"]] == ["M"]
    assert api(MANAGER).get("/partners/products").json() == []
