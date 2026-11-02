"""Delivery pages: reminders, refresh without a key, the waybill section of the order card (test waybills)."""

from tests.seed import ADMIN, BRANCH_1_REF, KYIV_REF, MANAGER, SHIPPING_ORDER_ID, STOREKEEPER, WAITING_TTN

HTMX = {"HX-Request": "true"}


def test_reminders_and_refresh_without_a_key(api):
    client = api(ADMIN)
    page = client.get("/app/delivery").text
    assert WAITING_TTN in page

    done = client.post("/app/delivery/reminders/2/done", headers=HTMX).text
    assert "Нікому нагадувати не треба." in done
    assert 'role="alert"' in client.post("/app/delivery/reminders/999/done", headers=HTMX).text
    assert "Ключ API Нової Пошти не налаштовано" in client.post("/app/delivery/tracking/refresh", headers=HTMX).text
    assert api(STOREKEEPER).get("/app/delivery").status_code == 403


def test_city_and_branch_pickers(api):
    client = api(MANAGER)
    cities = client.get("/app/delivery/cities?city_search=Ки", headers=HTMX).text
    assert f'value="{KYIV_REF}"' in cities
    assert "load" in cities  # a found city loads its branches at once
    branches = client.get(f"/app/delivery/warehouses?city_ref={KYIV_REF}", headers=HTMX).text
    assert f'value="{BRANCH_1_REF}"' in branches
    assert "wh-kyiv-3" not in branches  # closed


def test_order_card_creates_prints_and_deletes_a_test_waybill(api):
    client = api(MANAGER)
    url = f"/app/delivery/orders/{SHIPPING_ORDER_ID}/shipment"
    form_html = client.get(url, headers=HTMX).text
    assert "тестову ТТН" in form_html
    assert f'<option value="{BRANCH_1_REF}" selected>' in form_html
    assert 'name="cash_on_delivery" value="1200"' in form_html

    form = {"first_name": "Тарас", "last_name": "Клієнт", "middle_name": "", "phone": "0670000005",
            "city_ref": KYIV_REF, "warehouse_ref": BRANCH_1_REF, "declared_cost": "1200", "cash_on_delivery": "1200",
            "box": "Маленька"}  # fmt: skip
    closed = client.post(url, data=form | {"warehouse_ref": "wh-kyiv-3"}, headers=HTMX)
    assert "Такого відділення в цьому місті немає в довіднику." in closed.text
    custom = {"box": "", "weight": "1", "width": "30", "length": "30", "height": "важко"}
    assert "Висота: потрібне число" in client.post(url, data=form | custom, headers=HTMX).text

    created = client.post(url, data=form, headers=HTMX)
    assert created.headers["HX-Retarget"] == "#shipment"
    assert "ТТН створено." in created.text
    number = client.get(f"/orders/{SHIPPING_ORDER_ID}/shipment").json()["number"]
    assert number in created.text

    printed = client.get(f"{url}/print?kind=Markings")
    assert printed.status_code == 400  # no printing without a key
    assert "Не вдалося надрукувати" in printed.text
    assert "ТТН видалено." in client.post(f"{url}/delete", headers=HTMX).text
    assert client.get(f"/orders/{SHIPPING_ORDER_ID}/shipment").status_code == 204
