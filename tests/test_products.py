"""Shop products (ported from the Java ProductControllerTest). Its own database: the test changes products.

Next to the seed product 102 ("Сорочка лляна оверсайз") come invented ones: 70 and 72 are dresses, 71 is disabled.
"""

from decimal import Decimal

import pytest

from newerp.domain.catalog import ProductChange
from newerp.domain.common import DomainError
from tests.seed import CATEGORY_PRODUCT_ID, LANGUAGE_ID, MANAGER, STOREKEEPER, insert, product

DRESS_VERSION = "2026-10-01T10:00:00"
OTHER_VERSION = "2026-01-01T00:00:00"
LABELS = {1, 2}
CATEGORIES = {30, 31}


# ═══ ProductChange ═══


def test_none_parts_stay_as_they_are():
    assert ProductChange.of(None, None, None, None, LABELS, CATEGORIES) == ProductChange()


def test_price_is_not_negative_with_two_decimals():
    assert ProductChange.of(Decimal("1499.90"), None, None, None, LABELS, CATEGORIES).price == Decimal("1499.9")
    assert ProductChange.of(Decimal("1500.0000"), None, None, None, LABELS, CATEGORIES).price == 1500
    with pytest.raises(DomainError, match="Ціна — не менше 0, не більше двох знаків після коми."):
        ProductChange.of(Decimal(-1), None, None, None, LABELS, CATEGORIES)
    with pytest.raises(DomainError):
        ProductChange.of(Decimal("10.005"), None, None, None, LABELS, CATEGORIES)


def test_labels_and_categories_must_be_known_and_categories_not_empty():
    change = ProductChange.of(None, False, [2, 2], [31], LABELS, CATEGORIES)
    assert (change.label_ids, change.category_ids) == ([2], [31])
    assert ProductChange.of(None, None, [], None, LABELS, CATEGORIES).label_ids == []
    with pytest.raises(DomainError, match="Такої мітки немає."):
        ProductChange.of(None, None, [9], None, LABELS, CATEGORIES)
    with pytest.raises(DomainError, match="Оберіть хоча б одну категорію."):
        ProductChange.of(None, None, None, [], LABELS, CATEGORIES)
    with pytest.raises(DomainError, match="Такої категорії немає або вона вимкнена."):
        ProductChange.of(None, None, None, [32], LABELS, CATEGORIES)


# ═══ API ═══


def description(db, product_id: int, name: str) -> None:
    insert(
        db, "dd_product_description", product_id=product_id, language_id=LANGUAGE_ID, name=name, description="",
        tag="", meta_title="", meta_description="", meta_keyword="",
    )  # fmt: skip


def size(db, value_id: int, product_id: int, option_value_id: int, quantity: int) -> None:
    insert(
        db, "dd_product_option_value", product_option_value_id=value_id, product_option_id=7, product_id=product_id,
        option_id=11, option_value_id=option_value_id, quantity=quantity, subtract=False, price=0, price_prefix="+",
        points=0, points_prefix="+", weight=0, weight_prefix="+",
    )  # fmt: skip


def category(db, category_id: int, name: str, enabled: bool) -> None:
    insert(db, "dd_category", category_id=category_id, status=enabled, top=False, column=0,
           date_added="2026-01-01", date_modified="2026-01-01")  # fmt: skip
    insert(
        db, "dd_category_description", category_id=category_id, language_id=LANGUAGE_ID, name=name, description="",
        meta_title="", meta_description="", meta_keyword="",
    )  # fmt: skip


def label(db, label_id: int, name: str, sort_order: int) -> None:
    insert(db, "dd_product_label", label_id=label_id, name=name, background_color="", text_color="",
           sort_order=sort_order, status=True)  # fmt: skip


@pytest.fixture(scope="module")
def catalog(db):
    product(db, 70, "SL-01", 1500)
    product(db, 71, "P-02", 500)
    product(db, 72, "SL-50%", 900)
    with db.cursor() as cursor:
        cursor.execute("UPDATE dd_product SET quantity = 6, date_modified = '2026-10-01 10:00' WHERE product_id = 70")
        cursor.execute("UPDATE dd_product SET status = 0 WHERE product_id = 71")
    for product_id, name in ((70, "Сукня лляна"), (71, "Пояс шкіряний"), (72, "Сукня шовкова")):
        description(db, product_id, name)
    for option_value_id, name in ((902, "S"), (903, "L")):
        insert(db, "dd_option_value_description", option_value_id=option_value_id, language_id=LANGUAGE_ID,
               option_id=11, name=name)  # fmt: skip
    size(db, 701, 70, 902, 4)
    size(db, 702, 70, 903, 2)
    category(db, 30, "Сукні", True)
    category(db, 31, "Аксесуари", True)
    category(db, 32, "Архів", False)
    for product_id, category_id in ((70, 30), (72, 30), (71, 31)):
        insert(db, "dd_product_to_category", product_id=product_id, category_id=category_id)
    label(db, 1, "Хіт", 1)
    label(db, 2, "Новинка", 2)
    insert(db, "dd_product_to_label", product_id=70, label_id=2)
    insert(db, "dd_product_to_label", product_id=70, label_id=1)


def test_list_filters_pages_card_and_lookups(api, catalog):
    manager = api(MANAGER)

    def ids(query: str = "") -> list[int]:
        return [p["id"] for p in manager.get("/catalog/products" + query).json()["items"]]

    everything = manager.get("/catalog/products").json()
    assert everything["totalCount"] == 4
    assert [p["id"] for p in everything["items"]] == [71, CATEGORY_PRODUCT_ID, 70, 72]  # by name
    assert next(p for p in everything["items"] if p["id"] == 70) == {
        "id": 70, "name": "Сукня лляна", "model": "SL-01", "price": 1500, "enabled": True, "quantity": 6, "image": None,
    }  # fmt: skip
    assert ids("?categoryId=30&enabled=true") == [70, 72]
    assert ids("?labelId=1") == [70]
    assert ids("?enabled=false") == [71]
    # % in the search is a plain character, not a LIKE pattern; a number finds the product itself.
    assert ids("?search=50%25") == [72]
    assert ids("?search=71") == [71]
    second = manager.get("/catalog/products?page=2&pageSize=2").json()
    assert (second["page"], second["totalPages"], [p["id"] for p in second["items"]]) == (2, 2, [70, 72])

    card = manager.get("/catalog/products/70").json()
    assert (card["name"], card["model"], card["dateModified"]) == ("Сукня лляна", "SL-01", DRESS_VERSION)
    assert card["sizes"] == [
        {"optionValueId": 902, "name": "S", "quantity": 4},
        {"optionValueId": 903, "name": "L", "quantity": 2},
    ]
    assert card["labels"] == [{"id": 1, "name": "Хіт"}, {"id": 2, "name": "Новинка"}]
    assert card["categories"] == [{"id": 30, "name": "Сукні"}]
    assert manager.get("/catalog/products/999").status_code == 404
    assert [x["name"] for x in manager.get("/catalog/products/labels").json()] == ["Хіт", "Новинка"]
    # A disabled category is not offered.
    assert [x["name"] for x in manager.get("/catalog/products/categories").json()] == ["Аксесуари", "Сукні"]

    assert api(STOREKEEPER).get("/catalog/products").status_code == 403


def test_patch_changes_only_the_version_seen_and_refuses_bad_values(api, catalog):
    manager = api(MANAGER)
    request = {
        "price": 1399.50,
        "enabled": False,
        "labelIds": [2],
        "categoryIds": [30, 31],
        "dateModified": DRESS_VERSION,
    }

    changed = manager.patch("/catalog/products/70", json=request)

    assert changed.status_code == 200
    card = changed.json()
    assert (card["price"], card["enabled"]) == (1399.5, False)
    assert [x["id"] for x in card["labels"]] == [2]
    assert sorted(x["id"] for x in card["categories"]) == [30, 31]
    assert card["dateModified"] != DRESS_VERSION
    assert len(card["sizes"]) == 2  # sizes and stock are not touched
    # The same old version again: the product changed since (here, by us).
    stale = manager.patch("/catalog/products/70", json={"price": 1000, "dateModified": DRESS_VERSION})
    assert stale.status_code == 409
    assert manager.get("/catalog/products/70").json()["price"] == 1399.5

    # None keeps the value: in 72 only the labels change.
    labels_only = manager.patch("/catalog/products/72", json={"labelIds": [1], "dateModified": OTHER_VERSION}).json()
    assert (labels_only["price"], labels_only["enabled"]) == (900, True)
    assert labels_only["labels"] == [{"id": 1, "name": "Хіт"}]
    assert [x["id"] for x in labels_only["categories"]] == [30]

    for bad in (
        {"price": -1},
        {"price": 10.005},
        {"labelIds": [9]},
        {"categoryIds": []},
        {"categoryIds": [32]},  # category 32 is disabled: it cannot be chosen again
    ):
        assert manager.patch("/catalog/products/71", json={**bad, "dateModified": OTHER_VERSION}).status_code == 400
    assert manager.patch("/catalog/products/71", json={"price": 100}).status_code == 400
    assert manager.patch("/catalog/products/999", json={"price": 100, "dateModified": OTHER_VERSION}).status_code == 404
    # The refused changes did not touch the product.
    untouched = manager.get("/catalog/products/71").json()
    assert (untouched["price"], untouched["dateModified"]) == (500, OTHER_VERSION)
    assert [x["id"] for x in untouched["categories"]] == [31]

    forbidden = api(STOREKEEPER).patch("/catalog/products/70", json={"price": 1, "dateModified": DRESS_VERSION})
    assert forbidden.status_code == 403
