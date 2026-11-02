"""Reports on what the storefront records: online visitors, product views, reward points, taxes. Own database."""

import pytest

from tests.seed import CATEGORY_PRODUCT_ID, LANGUAGE_ID, MANAGER, ORDER_ID, STOREKEEPER, insert

CUSTOMER_ID = 1


@pytest.fixture(scope="module")
def storefront(db):
    insert(
        db, "dd_customer", customer_id=CUSTOMER_ID, customer_group_id=1, language_id=LANGUAGE_ID, firstname="Олена",
        lastname="Коваль", email="olena@example.com", telephone="0930000000", fax="", password="", salt="",
        custom_field="", ip="", status=True, safe=False, token="", code="", address_id=0, date_added="2024-05-01",
    )  # fmt: skip
    insert(db, "dd_customer_online", ip="192.0.2.10", customer_id=CUSTOMER_ID, url="https://shop.test/sukni",
           referer="https://instagram.com/", date_added="2026-11-02 10:00:00")  # fmt: skip
    insert(db, "dd_customer_online", ip="192.0.2.11", customer_id=0, url="https://shop.test/<script>", referer="",
           date_added="2026-11-02 10:05:00")  # fmt: skip
    for points, description in ((300, "За замовлення"), (-100, "Списано в замовленні")):
        insert(db, "dd_customer_reward", customer_id=CUSTOMER_ID, order_id=ORDER_ID, description=description,
               points=points, date_added="2026-10-05 12:00:00")  # fmt: skip
    with db.cursor() as cursor:
        cursor.execute("UPDATE dd_product SET viewed = 40 WHERE product_id = %s", (CATEGORY_PRODUCT_ID,))
    for order_id, value in ((ORDER_ID, 250), (2, 333.33)):
        insert(db, "dd_order_total", order_id=order_id, code="tax", title="ПДВ 20%", value=value, sort_order=5)


def test_storefront_reports(api, storefront):
    client = api(MANAGER)
    online = client.get("/reports/online").json()
    assert [(v["ip"], v["customer"]) for v in online] == [("192.0.2.11", None), ("192.0.2.10", "Олена Коваль")]

    [viewed] = client.get("/reports/product-views").json()
    assert (viewed["productId"], viewed["viewed"], viewed["sold"]) == (CATEGORY_PRODUCT_ID, 40, 1)
    assert viewed["percent"] == 2.5  # 1 sold per 40 views

    [reward] = client.get("/reports/rewards").json()
    assert (reward["customer"], reward["points"]) == ("Олена Коваль", 200)

    taxes = client.get("/reports/taxes?from=2026-10-01&to=2026-10-31&group=Month").json()
    assert [(r["title"], r["orders"], r["total"]) for r in taxes["rows"]] == [("ПДВ 20%", 2, 583.33)]
    assert api(STOREKEEPER).get("/reports/online").status_code == 403


def test_storefront_report_pages(api, storefront):
    client = api(MANAGER)
    online = client.get("/app/reports?view=online").text
    assert "Олена Коваль" in online
    assert "https://shop.test/&lt;script&gt;" in online  # what a visitor typed is escaped
    assert "200" in client.get("/app/reports?view=rewards").text
    assert "2,5" in client.get("/app/reports?view=views").text
    assert "ПДВ 20%" in client.get("/app/reports?view=taxes&since=2026-10-01&until=2026-10-31").text
