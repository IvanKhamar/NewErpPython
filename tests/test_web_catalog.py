"""Catalog pages: products and their card, the product sort, pre-orders."""

from tests.seed import CATEGORY_ID, CATEGORY_PRODUCT_ID, MANAGER, STOREKEEPER
from tests.test_pre_orders import SIZE_M
from tests.test_product_sort import add_catalog

HTMX = {"HX-Request": "true"}


def test_product_card_saves_only_the_version_it_showed(api):
    client = api(MANAGER)
    found = client.get("/app/catalog/products?search=лляна", headers=HTMX).text
    assert f'href="/app/catalog/products/{CATEGORY_PRODUCT_ID}"' in found

    card = client.get(f"/app/catalog/products/{CATEGORY_PRODUCT_ID}").text
    assert 'name="date_modified"' in card
    version = client.get(f"/catalog/products/{CATEGORY_PRODUCT_ID}").json()["dateModified"]
    form = {"date_modified": version, "price": "999,50", "enabled": "1", "category_id": str(CATEGORY_ID)}
    saved = client.post(f"/app/catalog/products/{CATEGORY_PRODUCT_ID}", data=form, headers=HTMX)
    assert "Зміни збережено." in saved.text
    assert client.get(f"/catalog/products/{CATEGORY_PRODUCT_ID}").json()["price"] == 999.5

    stale = client.post(f"/app/catalog/products/{CATEGORY_PRODUCT_ID}", data=form, headers=HTMX)
    assert 'role="alert"' in stale.text  # the product changed since this version: 409 as a message
    assert api(STOREKEEPER).get("/app/catalog/products").status_code == 403


def test_sort_preview_pin_write_and_restore(api, db):
    add_catalog(db)
    client = api(MANAGER)
    page = client.get("/app/catalog/sort?group=1:30&days=3650").text
    assert '<option value="1:30" selected>' in page

    pinned = client.post("/app/catalog/sort/pin", data={"group": "1:30", "days": "3650", "position": "1",
                                                       "product_id": "203"}, headers=HTMX)  # fmt: skip
    assert "Товар закріплено." in pinned.text
    assert "закріплено</span>" in pinned.text
    written = client.post("/app/catalog/sort/recalculate", data={"group": "1:30", "days": "3650"}, headers=HTMX)
    assert "Порядок записано" in written.text

    snapshot_id = client.get("/catalog/sort/snapshots").json()[0]["id"]
    restored = client.post("/app/catalog/sort/restore", data={"snapshot_id": str(snapshot_id), "group": "1:30"})
    assert "Порядок повернуто зі знімка." in restored.text
    assert 'role="alert"' in client.post("/app/catalog/sort/pin", data={"group": "1:30", "position": ""}).text
    assert "Оберіть групу" in client.post("/app/catalog/sort/recalculate", data={"group": "x"}).text


def test_pre_order_limit_and_date(api):
    client = api(MANAGER)
    page = client.get("/app/preorders?search=лляна").text
    assert f'hx-post="/app/preorders/{SIZE_M}"' in page

    saved = client.post(f"/app/preorders/{SIZE_M}", data={"quota": "5", "date_available": "2026-11-20"}, headers=HTMX)
    assert "Збережено: ліміт 5." in saved.text
    [row] = client.get("/preorders?onlyWithQuota=true").json()
    assert (row["quota"], row["dateAvailable"]) == (5, "2026-11-20")
    assert "ціле число" in client.post(f"/app/preorders/{SIZE_M}", data={"quota": "1,5"}).text
    assert "Дата" in client.post(f"/app/preorders/{SIZE_M}", data={"quota": "1", "date_available": "завтра"}).text
    assert "Списано продане наперед" in client.post("/app/preorders/settle", headers=HTMX).text
