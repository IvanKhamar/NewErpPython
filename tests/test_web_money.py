"""Cashbox and bank payment pages, and the payment and cheque sections of the order card (stubs, no keys)."""

from tests.seed import ADMIN, CASHIER_ID, MANAGER, ORDER_ID, STOREKEEPER

HTMX = {"HX-Request": "true"}


def test_shift_cheque_from_the_order_card_and_z_report(api):
    client = api(MANAGER)
    page = client.get("/app/cashbox").text
    assert "тестова заглушка" in page
    assert "Новий касир" not in page  # the setup is the admin's

    cheque_url = f"/app/cashbox/orders/{ORDER_ID}/cheque"
    assert "Видати чек" in client.get(cheque_url, headers=HTMX).text
    assert 'role="alert"' in client.post(cheque_url, data={"cashier_id": ""}, headers=HTMX).text  # no open shift

    assert "Зміну відкрито." in client.post(f"/app/cashbox/cashiers/{CASHIER_ID}/open", headers=HTMX).text
    issued = client.post(cheque_url, data={"cashier_id": ""}, headers=HTMX).text
    assert "Чек видано." in issued
    assert "Фіскалізовано" in issued
    assert "ТЕСТОВИЙ" in client.post(f"/app/cashbox/cashiers/{CASHIER_ID}/x-report", headers=HTMX).text

    closed = client.post(f"/app/cashbox/cashiers/{CASHIER_ID}/close", headers=HTMX).text
    assert "Z-звіт збережено." in closed
    report_id = client.get("/cashbox/reports").json()[0]["id"]
    assert "<pre>" in client.get(f"/app/cashbox/reports/{report_id}/text", headers=HTMX).text
    cheque_id = client.get(f"/orders/{ORDER_ID}/cheque").json()["id"]
    assert 'role="alert"' in client.post(f"/app/cashbox/cheques/{cheque_id}/delete", headers=HTMX).text
    assert api(STOREKEEPER).get("/app/cashbox").status_code == 403


def test_admin_sets_up_points_of_sale_and_cashiers(api):
    admin = api(ADMIN)
    assert api(MANAGER).get("/app/cashbox/cashiers/new").status_code == 403

    added = admin.post(
        "/app/cashbox/directory/warehouses", data={"name": "Магазин веб", "description": ""}, headers=HTMX
    )
    assert "Магазин веб" in added.text
    warehouse_id = next(w["id"] for w in admin.get("/cashbox/setup").json()["warehouses"] if w["name"] == "Магазин веб")

    form = {"cashier_id": "", "name": "Ігор веб", "description": "", "payment_code": "cod", "auto_close_time": "21:30",
            "warehouse_id": str(warehouse_id), "requisites_id": "", "is_active": "1"}  # fmt: skip
    wrong = admin.post("/app/cashbox/cashiers/save", data=form | {"auto_close_time": "пізно"})
    assert wrong.status_code == 400
    assert "Час автозакриття" in wrong.text
    assert admin.post("/app/cashbox/cashiers/save", data=form, follow_redirects=False).status_code == 303
    igor = next(c for c in admin.get("/cashbox/setup").json()["cashiers"] if c["name"] == "Ігор веб")
    assert (igor["warehouse"], igor["autoCloseTime"]) == ("Магазин веб", "21:30:00")
    assert 'value="21:30"' in admin.get(f"/app/cashbox/cashiers/{igor['id']}/edit").text

    in_use = admin.post(f"/app/cashbox/directory/warehouses/{warehouse_id}/delete", headers=HTMX)
    assert 'role="alert"' in in_use.text


def test_payment_is_matched_unlinked_and_hidden(api):
    client = api(MANAGER)
    [payment] = client.get("/payments/bank?linked=false").json()["items"]
    page = client.get("/app/payments?linked=no")
    assert f'id="payment-{payment["id"]}"' in page.text

    found = client.get(f"/app/payments/{payment['id']}/suggestions", headers=HTMX).text
    assert 'href="/app/orders/2"' in found
    assert "сума, прізвище" in found

    url = f"/app/payments/{payment['id']}"
    linked = client.post(f"{url}/order", data={"order_id": "2"}, headers=HTMX)
    assert 'href="/app/orders/2"' in linked.text
    assert "Відв'язати" in linked.text
    assert "Тарас Клієнт2" in client.get("/app/payments/orders/2", headers=HTMX).text  # the order card section

    missing = client.post(f"{url}/order", data={"order_id": "999"}, headers=HTMX)
    assert missing.headers["HX-Retarget"] == f"#match-{payment['id']}"
    assert client.post(f"{url}/visible", data={"visible": "0"}, headers=HTMX).text == ""
    assert client.get("/payments/bank").json()["items"] == []
    assert "Ключів ПриватБанку немає" in client.post("/app/payments/import", data={}, headers=HTMX).text


def test_liqpay_link_from_the_order_card(api):
    client = api(MANAGER)
    section = client.post("/app/payments/orders/2/liqpay-link", data={"amount": "500"}, headers=HTMX).text
    assert "https://www.liqpay.ua/api/3/checkout?data=" in section
    assert "Ще не оплачено" in section
    assert 'role="alert"' in client.post("/app/payments/orders/2/liqpay-link", data={"amount": "2000,01"}).text
