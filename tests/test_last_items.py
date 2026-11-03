"""Status lists (69), banners and SEO addresses (70), OpenCart's standard reports (71)."""

import pytest

from tests.seed import ADMIN, CATEGORY_PRODUCT_ID, LANGUAGE_ID, MANAGER, PENDING, STOREKEEPER, insert

HTMX = {"HX-Request": "true"}


def test_status_lists_rename_add_and_keep_used_ones(api):
    admin = api(ADMIN)
    statuses = {s["id"]: s for s in admin.get("/admin/status-lists/order-statuses").json()}
    assert statuses[PENDING]["used"] > 0
    renamed = admin.put(
        f"/admin/status-lists/order-statuses/{PENDING}", json={"name": "1) Уточнюємо!", "color": "#2e7d32"}
    )
    assert renamed.json()["color"] == "#2e7d32"
    assert (
        admin.put(f"/admin/status-lists/order-statuses/{PENDING}", json={"name": "X", "color": "red;x"}).status_code
        == 400
    )
    assert admin.delete(f"/admin/status-lists/order-statuses/{PENDING}").status_code == 400  # orders have it

    stock = admin.post("/admin/status-lists/stock-statuses", json={"name": "Під замовлення"})
    assert stock.status_code == 201
    stock = stock.json()
    assert stock["color"] == ""  # only order statuses have a colour
    second = admin.post("/admin/status-lists/stock-statuses", json={"name": "Є"}).json()
    assert second["id"] == stock["id"] + 1  # the id shared by the languages: the largest plus one
    assert admin.delete(f"/admin/status-lists/stock-statuses/{stock['id']}").status_code == 204
    assert admin.get("/admin/status-lists/colours").status_code == 400
    assert api(MANAGER).get("/admin/status-lists/order-statuses").status_code == 403


def test_banners_take_only_safe_links_and_pictures(api):
    client = api(MANAGER)
    banner = client.post("/catalog/banners", json={"name": "Головний", "images": [
        {"title": "Осінь", "link": "/catalog/autumn", "image": "catalog/banners/autumn.jpg", "sortOrder": 1},
        {"title": "Без картинки", "link": "/x", "image": ""},
    ]})  # fmt: skip
    assert banner.status_code == 201, banner.text
    banner = banner.json()
    assert [i["title"] for i in banner["images"]] == ["Осінь"]
    for image in ({"link": "javascript:alert(1)", "image": "catalog/a.jpg"}, {"link": "/x", "image": "../a.jpg"}):
        assert client.post("/catalog/banners", json={"name": "X", "images": [image]}).status_code == 400
    assert client.put(f"/catalog/banners/{banner['id']}", json=banner | {"enabled": False}).json()["enabled"] is False
    assert client.delete(f"/catalog/banners/{banner['id']}").status_code == 204


def test_seo_addresses_are_unique_and_point_to_known_pages(api):
    client = api(MANAGER)
    created = client.post(
        "/catalog/seo-urls", json={"query": f"product_id={CATEGORY_PRODUCT_ID}", "keyword": "sorochka"}
    )
    assert created.status_code == 201
    created = created.json()
    assert client.get("/catalog/seo-urls?search=soroch").json()["items"][0]["keyword"] == "sorochka"
    assert client.post("/catalog/seo-urls", json={"query": "product_id=999", "keyword": "sorochka"}).status_code == 409
    same_page = {"query": f"product_id={CATEGORY_PRODUCT_ID}", "keyword": "insha"}
    assert client.post("/catalog/seo-urls", json=same_page).status_code == 409
    for bad in ({"query": "route=account/login", "keyword": "x"}, {"query": "product_id=1 OR 1", "keyword": "x"},
                {"query": "product_id=5", "keyword": "Сорочка"}):  # fmt: skip
        assert client.post("/catalog/seo-urls", json=bad).status_code == 400
    assert (
        client.put(f"/catalog/seo-urls/{created['id']}", json=created | {"keyword": "sorochka-lion"}).status_code == 200
    )
    assert client.delete(f"/catalog/seo-urls/{created['id']}").status_code == 204


@pytest.fixture(scope="module")
def activity(db):
    insert(db, "dd_customer_search", customer_id=0, keyword="льон", products=4, date_added="2026-10-02 10:00:00",
           store_id=0, language_id=LANGUAGE_ID, sub_category=0, description=0, ip="10.0.0.1")  # fmt: skip
    insert(db, "dd_customer_search", customer_id=0, keyword="льон", products=2, date_added="2026-10-03 10:00:00",
           store_id=0, language_id=LANGUAGE_ID, sub_category=0, description=0, ip="10.0.0.1")  # fmt: skip
    insert(db, "dd_statistics", code="order_sale", value=12345)


@pytest.mark.parametrize(
    "kind",
    [
        "sales-orders",
        "sales-returns",
        "sales-shipping",
        "sales-coupons",
        "products-purchased",
        "customer-orders",
        "customer-transactions",
        "customer-activity",
        "customer-searches",
        "statistics",
    ],  # fmt: skip
)
def test_every_standard_report_runs(api, activity, kind):
    for group in ("day", "week", "month"):
        response = api(MANAGER).get(f"/reports/standard/{kind}?from=2026-09-01&to=2026-11-30&group={group}")
        assert response.status_code == 200, response.text
        report = response.json()
        assert len(report["columns"]) >= 2 and all(len(row) == len(report["columns"]) for row in report["rows"])


def test_report_numbers(api, activity):
    client = api(MANAGER)
    orders = client.get("/reports/standard/sales-orders?from=2026-10-01&to=2026-10-31&group=month").json()
    assert orders["rows"][0][0] == "2026-10-01" and orders["rows"][0][1] >= 2  # orders 1 and 2 of the seed
    searches = client.get("/reports/standard/customer-searches?from=2026-10-01&to=2026-10-31").json()
    assert searches["rows"] == [["льон", 2, 4, "2026-10-03"]]
    assert client.get("/reports/standard/statistics").json()["rows"] == [["order_sale", 12345.0]]
    assert client.get("/reports/standard/sales-orders?from=2026-10-31&to=2026-10-01").status_code == 400
    assert client.get("/reports/standard/sales-orders?from=2024-01-01&to=2026-01-01").status_code == 400
    assert client.get("/reports/standard/everything").status_code == 400
    assert api(STOREKEEPER).get("/reports/standard/statistics").status_code == 403


def test_web_pages_of_the_last_items(api):
    admin, manager = api(ADMIN), api(MANAGER)
    assert admin.get("/app/admin/statuses?kind=return-reasons").status_code == 200
    assert manager.get("/app/admin/statuses").status_code == 403
    added = admin.post("/app/admin/statuses/return-reasons/0/save", data={"name": "Не підійшов розмір"}, headers=HTMX)
    assert "Збережено." in added.text and 'value="Не підійшов розмір"' in added.text

    assert manager.get("/app/catalog/banners").status_code == 200
    banner = manager.post("/app/catalog/banners/0/save", headers=HTMX, data={
        "name": "Веб", "enabled": "1", "image_title": ["Літо", ""], "image_link": ["/summer", ""],
        "image_image": ["catalog/b/summer.jpg", ""], "image_sort_order": ["1", ""],
    })  # fmt: skip
    assert "Збережено." in banner.text and 'value="catalog/b/summer.jpg"' in banner.text
    seo = manager.post("/app/catalog/seo-urls/0/save", data={"query": "category_id=5", "keyword": "sorochky"},
                       headers=HTMX)  # fmt: skip
    assert 'value="sorochky"' in seo.text

    page = manager.get("/app/reports/standard?kind=customer-searches&date_from=2026-10-01&date_to=2026-10-31")
    assert page.status_code == 200 and "Пошук на вітрині" in page.text
    bad = manager.get("/app/reports/standard?kind=sales-orders&date_from=nope", headers=HTMX)
    assert 'class="error"' in bad.text
