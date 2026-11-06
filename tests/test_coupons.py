"""Coupon admin with the products and categories a coupon works on or never works on. Its own database."""

from datetime import date
from decimal import Decimal

import pytest

from newerp.domain.common import DomainError
from newerp.domain.marketing import Coupon
from tests.seed import CATEGORY_ID, CATEGORY_PRODUCT_ID, LANGUAGE_ID, MANAGER, ORDER_ID, STOREKEEPER, insert
from tests.test_partners import add_catalog_data

HTMX = {"HX-Request": "true"}
START, END = date(2026, 11, 1), date(2026, 11, 30)


def coupon(**changes):
    values = dict(name="Осінь", code="autumn10", type="P", discount=Decimal(10), total=Decimal(0), logged=False,
                  shipping=False, start=START, end=END, uses_total=0, uses_customer=1, active=True, product_ids=[],
                  category_ids=[], excluded_product_ids=[], excluded_category_ids=[]) | changes  # fmt: skip
    return Coupon.of(**values)


def test_coupon_rules():
    assert coupon().code == "AUTUMN10"
    assert coupon(product_ids=[5, 5, 0, 3]).product_ids == (5, 3)
    for changes, message in (
        ({"name": "Оу"}, "Назва купона"),
        ({"code": "a-1"}, "Код купона"),
        ({"type": "X"}, "Тип знижки"),
        ({"discount": Decimal(101)}, "не більше 100"),
        ({"discount": Decimal("0")}, "Знижка"),
        ({"end": date(2026, 10, 1)}, "закінчитися раніше"),
        ({"start": None}, "з якого й до якого"),
        ({"uses_total": -1}, "від'ємною"),
        ({"category_ids": [5], "excluded_category_ids": [5]}, "водночас"),
    ):
        with pytest.raises(DomainError, match=message):
            coupon(**changes)
    assert coupon(type="F", discount=Decimal(500)).discount == 500  # a fixed sum may be above 100


@pytest.fixture(scope="module")
def catalog(db):
    add_catalog_data(db)  # the category name
    insert(db, "dd_category", category_id=CATEGORY_ID, status=True, top=False, column=0, date_added="2026-01-01",
           date_modified="2026-01-01")  # fmt: skip  # an enabled category, for the page


def test_coupon_admin_with_exclusions_history_and_delete(api, db, catalog):
    client = api(MANAGER)
    body = {"name": "Осінній розпродаж", "code": "autumn10", "type": "P", "discount": 10, "total": 1000,
            "dateStart": "2026-11-01", "dateEnd": "2026-11-30", "usesTotal": 0, "usesCustomer": 1,
            "categoryIds": [CATEGORY_ID], "excludedProductIds": [CATEGORY_PRODUCT_ID]}  # fmt: skip
    assert client.post("/marketing/coupons", json=body | {"excludedProductIds": [999999]}).status_code == 400
    created = client.post("/marketing/coupons", json=body)
    assert created.status_code == 200, created.text
    card = created.json()
    assert (card["code"], card["categories"][0]["name"]) == ("AUTUMN10", "Сорочки")
    assert [p["id"] for p in card["excludedProducts"]] == [CATEGORY_PRODUCT_ID]
    assert client.post("/marketing/coupons", json=body).status_code == 400  # the code is taken

    swap = {"productIds": [CATEGORY_PRODUCT_ID], "excludedProductIds": []}
    changed = client.put(f"/marketing/coupons/{card['id']}", json=body | swap).json()
    assert ([p["id"] for p in changed["products"]], changed["excludedProducts"]) == ([CATEGORY_PRODUCT_ID], [])
    [listed] = client.get("/marketing/coupons?search=autumn").json()
    assert (listed["used"], listed["dateEnd"]) == (0, "2026-11-30")

    insert(db, "dd_coupon_history", coupon_id=card["id"], order_id=ORDER_ID, customer_id=0, amount=-150,
           date_added="2026-11-02 10:00:00")  # fmt: skip
    refused = client.delete(f"/marketing/coupons/{card['id']}")
    assert refused.json()["detail"] == "Купон уже використали в замовленнях — його можна лише вимкнути."
    assert client.get(f"/marketing/coupons/{card['id']}").json()["history"][0]["orderId"] == ORDER_ID
    assert api(STOREKEEPER).get("/marketing/coupons").status_code == 403


def test_changes_to_a_coupon_are_in_the_audit_log(api, catalog, caplog):
    client = api(MANAGER)
    body = {"name": "Аудит", "code": "audit5", "type": "P", "discount": 5, "total": 0, "dateStart": "2026-11-01",
            "dateEnd": "2026-11-30", "usesTotal": 0, "usesCustomer": 1}  # fmt: skip
    coupon_id = client.post("/marketing/coupons", json=body).json()["id"]
    assert client.put(f"/marketing/coupons/{coupon_id}", json=body | {"discount": 7}).status_code == 200
    assert client.delete(f"/marketing/coupons/{coupon_id}").status_code == 204
    assert f"coupon created: {coupon_id}, P 5" in caplog.text
    assert f"coupon changed: {coupon_id}, P 7" in caplog.text
    assert f"coupon deleted: {coupon_id}, by user" in caplog.text


def test_saving_the_form_keeps_a_disabled_category_of_the_coupon(api, db, catalog):
    client = api(MANAGER)
    insert(db, "dd_category", category_id=777, status=False, top=False, column=0, date_added="2026-01-01",
           date_modified="2026-01-01")  # fmt: skip
    insert(db, "dd_category_description", category_id=777, language_id=LANGUAGE_ID, name="Архів", description="",
           meta_title="", meta_description="", meta_keyword="")  # fmt: skip
    body = {"name": "Архівний", "code": "archive1", "type": "P", "discount": 5, "total": 0, "dateStart": "2026-11-01",
            "dateEnd": "2026-11-30", "usesTotal": 0, "usesCustomer": 1, "categoryIds": [777]}  # fmt: skip
    coupon_id = client.post("/marketing/coupons", json=body).json()["id"]

    page = client.get(f"/app/marketing/coupons/{coupon_id}").text
    assert "Архів (вимкнена)" in page
    assert 'name="category_id" value="777" checked' in page

    form = {"coupon_id": str(coupon_id), "name": "Архівний", "code": "archive1", "type": "P", "discount": "6",
            "total": "0", "date_start": "2026-11-01", "date_end": "2026-11-30", "uses_total": "0",
            "uses_customer": "1", "active": "1", "product_ids": "", "excluded_product_ids": "",
            "category_id": "777"}  # fmt: skip
    assert client.post("/app/marketing/coupons/save", data=form, follow_redirects=False).status_code in (302, 303)
    assert [c["id"] for c in client.get(f"/marketing/coupons/{coupon_id}").json()["categories"]] == [777]


def test_coupon_pages(api, catalog):
    client = api(MANAGER)
    assert 'name="excluded_category_id"' in client.get("/app/marketing/coupons/new").text
    form = {"coupon_id": "", "name": "Зима", "code": "winter", "type": "F", "discount": "200", "total": "1500",
            "date_start": "2026-12-01", "date_end": "2026-12-31", "uses_total": "100", "uses_customer": "1",
            "active": "1", "product_ids": "", "excluded_product_ids": f"{CATEGORY_PRODUCT_ID}",
            "excluded_category_id": str(CATEGORY_ID)}  # fmt: skip
    wrong = client.post("/app/marketing/coupons/save", data=form | {"product_ids": "101, сто"})
    assert wrong.status_code == 400
    assert "не номер" in wrong.text
    assert 'value="winter"' in wrong.text  # the typed values come back
    saved = client.post("/app/marketing/coupons/save", data=form, follow_redirects=False)
    url = saved.headers["location"]
    page = client.get(url).text
    assert f'name="excluded_category_id" value="{CATEGORY_ID}" checked' in page
    assert f'value="{CATEGORY_PRODUCT_ID}"' in page
    assert "WINTER" in client.get("/app/marketing/coupons?search=winter", headers=HTMX).text
    assert client.post(f"{url}/delete", headers=HTMX).headers["HX-Redirect"] == "/app/marketing/coupons"
