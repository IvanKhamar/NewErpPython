"""The old admin's order page tools (items 35–42): shipping checks and discrepancies, customer edit, shipping date,
invoice and printing, reward points and commission, delete, Nova Poshta shipping cost."""

from datetime import datetime, timedelta

from tests import helpers, seed
from tests.seed import ADMIN, CATEGORY_PRODUCT_ID, MANAGER, PENDING, STOREKEEPER

NOVA_POSHTA = "novaposhta_warehouse.novaposhta_warehouse"
PICKUP = "pickup.pickup"
SHIPPED, AWAITING, ACCEPTED = 22, 21, 18
HTMX = {"HX-Request": "true"}


def new_order(client, phone: str, shipping_code: str = NOVA_POSHTA, quantity: int = 1) -> int:
    response = client.post(
        "/orders",
        json={
            "firstname": "Ольга",
            "lastname": "Перевірка",
            "telephone": phone,
            "statusId": PENDING,
            "shippingCode": shipping_code,
            "shippingMethod": "Нова Пошта" if shipping_code == NOVA_POSHTA else "Самовивіз",
            "city": "Київ",
            "address2": "Відділення №1",
            "paymentCode": "cod",
            "paymentMethod": "Накладений платіж",
            "lines": [
                {"productId": CATEGORY_PRODUCT_ID, "productOptionValueId": 502, "quantity": quantity, "price": 900}
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["orderId"]


def execute(db, sql: str, *args: object) -> None:
    with db.cursor() as cursor:
        cursor.execute(sql, args)


def waybill(db, order_id: int, number: str, created: datetime | None = None) -> None:
    seed.insert(
        db, "dd_novaposhta_document", order_id=order_id, carrier="novaposhta", ref=f"ref-{number}", number=number,
        cost=70, status="", status_code=1, recipient_address="Київ", redelivery=False,
        date_created=created or helpers.now(),
    )  # fmt: skip


def sms(db, phone: str, text: str) -> None:
    seed.insert(db, "dd_sms_log", order_id=0, pre_order_id=0, phone=phone, text=text, response="", date=helpers.now())


def issues(client, order_id: int, status_id: int) -> list[tuple[str, str]]:
    response = client.get(f"/orders/{order_id}/shipping-check?statusId={status_id}")
    assert response.status_code == 200, response.text
    return [(issue["level"], issue["text"]) for issue in response.json()["issues"]]


def test_shipped_needs_a_waybill_and_its_sms_to_the_order_phone(api, db):
    client = api(MANAGER)
    order_id = new_order(client, "+380671110001")

    assert issues(client, order_id, SHIPPED) == [("danger", "ТТН для цього замовлення не створена")]
    waybill(db, order_id, "20450000001111")
    assert issues(client, order_id, SHIPPED) == [("warning", "SMS з номером ТТН клієнту не надсилалась")]
    sms(db, "380509999999", "Ваша ТТН 2045 0000 0011 11")  # spaces in the text do not hide the number
    [(level, text)] = issues(client, order_id, SHIPPED)
    assert level == "danger" and "380509999999" in text
    sms(db, "0671110001", "ТТН 20450000001111")  # the same last 9 digits as +380671110001
    assert issues(client, order_id, SHIPPED) == []

    pickup = new_order(client, "+380671110002", PICKUP)
    assert issues(client, pickup, SHIPPED) == []  # pickups need no waybill
    assert issues(client, order_id, PENDING) == []  # other statuses are not checked
    assert client.get(f"/orders/999999/shipping-check?statusId={SHIPPED}").status_code == 404


def test_awaiting_shipment_is_only_for_pickups(api, db):
    client = api(MANAGER)
    pickup = new_order(client, "+380671110003", PICKUP)
    assert issues(client, pickup, AWAITING) == []

    [(level, _)] = issues(client, new_order(client, "+380671110004"), AWAITING)
    assert level == "warning"  # a Nova Poshta method: is it really a pickup?
    waybill(db, pickup, "20450000002222")
    [(level, text)] = issues(client, pickup, AWAITING)
    assert level == "danger" and "ТТН" in text


def test_discrepancies_list_stale_waybills_and_shipped_problems(api, db):
    client = api(MANAGER)
    stale = new_order(client, "+380671110005")
    assert client.put(f"/orders/{stale}/status", json={"statusId": ACCEPTED}).status_code == 200
    waybill(db, stale, "20450000003333", helpers.now() - timedelta(hours=5))
    fresh = new_order(client, "+380671110006")
    assert client.put(f"/orders/{fresh}/status", json={"statusId": ACCEPTED}).status_code == 200
    waybill(db, fresh, "20450000004444")  # made just now: still normal
    shipped = new_order(client, "+380671110007")
    assert client.put(f"/orders/{shipped}/status", json={"statusId": SHIPPED}).status_code == 200  # no waybill

    groups = client.get("/orders/discrepancies?days=3").json()
    assert len(groups) == 3
    stale_ids = [item["orderId"] for item in groups[0]["items"]]
    assert stale in stale_ids and fresh not in stale_ids
    assert {"orderId": shipped, "level": "danger"}.items() <= next(
        item for item in groups[1]["items"] if item["orderId"] == shipped
    ).items()
    assert client.get("/orders/discrepancies?days=500").status_code == 200  # cut to 30 days


def test_customer_data_can_be_edited_with_the_rules_of_a_new_order(api):
    client = api(MANAGER)
    order_id = new_order(client, "+380671110008")
    customer = client.get(f"/orders/{order_id}/customer").json()
    assert (customer["firstname"], customer["city"], customer["shippingCode"]) == ("Ольга", "Київ", NOVA_POSHTA)

    del customer["orderId"]
    changed = customer | {"lastname": "Нова", "city": "Львів", "house": "5", "flat": "12", "comment": "Дзвонити"}
    assert client.put(f"/orders/{order_id}/customer", json=changed).status_code == 204
    order = client.get(f"/orders/{order_id}").json()
    assert (order["customer"], order["shippingCity"], order["comment"]) == ("Ольга Нова", "Львів", "Дзвонити")

    assert client.put(f"/orders/{order_id}/customer", json=changed | {"shippingMethod": ""}).status_code == 400
    assert client.put(f"/orders/{order_id}/customer", json=changed | {"firstname": "я" * 33}).status_code == 400
    assert client.put("/orders/999999/customer", json=changed).status_code == 404
    assert api(STOREKEEPER).get(f"/orders/{order_id}/customer").status_code == 403


def test_shipping_date_is_saved_cleared_and_a_zero_date_reads_as_empty(api, db):
    client = api(MANAGER)
    order_id = new_order(client, "+380671110009")
    assert client.put(f"/orders/{order_id}/shipping-date", json={"dateShipping": "2026-11-20"}).status_code == 204
    assert client.get(f"/orders/{order_id}").json()["dateShipping"] == "2026-11-20"
    assert client.put(f"/orders/{order_id}/shipping-date", json={"dateShipping": None}).status_code == 204
    assert client.get(f"/orders/{order_id}").json()["dateShipping"] is None

    execute(db, "SET SESSION sql_mode = ''")  # the old site wrote zero dates
    execute(db, "UPDATE dd_order SET date_shipping = '0000-00-00' WHERE order_id = %s", order_id)
    assert client.get(f"/orders/{order_id}").json()["dateShipping"] is None


def test_invoice_numbers_follow_their_prefix_and_are_given_once(api, db):
    client = api(MANAGER)
    first, second = new_order(client, "+380671110010"), new_order(client, "+380671110011")
    for order_id in (first, second):
        execute(db, "UPDATE dd_order SET invoice_prefix = 'TST-' WHERE order_id = %s", order_id)

    assert client.post(f"/orders/{first}/invoice").json() == {"invoice": "TST-1"}
    assert client.post(f"/orders/{second}/invoice").json() == {"invoice": "TST-2"}
    assert client.post(f"/orders/{first}/invoice").json() == {"invoice": "TST-1"}  # kept
    assert client.get(f"/orders/{first}").json()["invoice"] == "TST-1"
    assert client.post("/orders/999999/invoice").status_code == 404


def test_print_pages_show_the_orders(api):
    client = api(MANAGER)
    first, second = new_order(client, "+380671110012", quantity=2), new_order(client, "+380671110013")

    invoice = client.get(f"/app/orders/print/invoice?id={first}&id={second}")
    assert invoice.status_code == 200
    assert invoice.text.count('class="page"') == 2
    assert "Сорочка лляна оверсайз" in invoice.text and "Розмір: M" in invoice.text
    shipping = client.get(f"/app/orders/print/shipping?id={first}")
    assert "Пакувальний лист" in shipping.text and "Ольга Перевірка" in shipping.text
    assert client.get("/app/orders/print/shipping?id=999999").status_code == 404
    assert client.get(f"/app/orders/print/other?id={first}").status_code == 400


def test_print_data_lists_the_orders_in_their_order_with_options_and_totals(api):
    # Ported from .NET OrderToolsApiTests.PrintData_ListsTheOrdersInTheirOrder_WithOptionsAndTotals (item 84).
    client = api(MANAGER)
    first, second = new_order(client, "+380671110014", quantity=2), new_order(client, "+380671110015")

    printed = client.get(f"/orders/print?ids={second}&ids={first}&ids=999999&ids={second}").json()
    assert [p["id"] for p in printed] == [second, first]
    [line] = printed[1]["lines"]
    assert (line["name"], line["options"], line["quantity"]) == ("Сорочка лляна оверсайз", "Розмір: M", 2)
    assert printed[1]["customer"] == "Ольга Перевірка"
    assert printed[1]["totals"] and set(printed[1]["store"]) == {"name", "address", "telephone", "email"}

    empty = client.get("/orders/print")
    assert (empty.status_code, empty.json()["detail"]) == (400, "Оберіть від 1 до 100 замовлень.")
    assert client.get("/orders/print?" + "&".join(f"ids={i}" for i in range(1, 102))).status_code == 400
    assert api(STOREKEEPER).get(f"/orders/print?ids={first}").status_code == 403


def test_reward_points_and_commission_are_added_once_and_taken_back(api, db):
    client = api(MANAGER)
    order_id = new_order(client, "+380671110014")
    assert client.post(f"/orders/{order_id}/reward").status_code == 400  # no customer account
    assert client.post(f"/orders/{order_id}/commission").status_code == 400  # not from a partner

    execute(db, "UPDATE dd_order SET customer_id = 77, affiliate_id = 88, commission = 45 WHERE order_id = %s",
            order_id)  # fmt: skip
    execute(db, "UPDATE dd_order_product SET reward = 15 WHERE order_id = %s", order_id)
    assert client.post(f"/orders/{order_id}/reward").json()["rewardAdded"] is True
    credits = client.post(f"/orders/{order_id}/reward").json()  # a second press adds nothing
    assert (credits["rewardPoints"], credits["customerId"]) == (15, 77)
    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*), SUM(points) FROM dd_customer_reward WHERE order_id = %s", (order_id,))
        assert cursor.fetchone() == (1, 15)

    assert client.post(f"/orders/{order_id}/commission").json()["commissionAdded"] is True
    with db.cursor() as cursor:
        cursor.execute("SELECT customer_id, amount FROM dd_customer_transaction WHERE order_id = %s", (order_id,))
        assert cursor.fetchall() == ((88, 45),)

    assert client.delete(f"/orders/{order_id}/reward").json()["rewardAdded"] is False
    assert client.delete(f"/orders/{order_id}/commission").json()["commissionAdded"] is False
    assert client.get("/orders/999999/credits").status_code == 404


def test_only_the_admin_deletes_and_only_an_order_nothing_points_to(api, db):
    manager, admin = api(MANAGER), api(ADMIN)
    order_id = new_order(manager, "+380671110015")
    assert manager.delete(f"/orders/{order_id}").status_code == 403

    assert admin.delete(f"/orders/{order_id}").status_code == 200
    assert admin.get(f"/orders/{order_id}").status_code == 404
    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM dd_order_product WHERE order_id = %s", (order_id,))
        assert cursor.fetchone()[0] == 0

    with_waybill = new_order(manager, "+380671110016")
    waybill(db, with_waybill, "20450000005555")
    refused = admin.delete(f"/orders/{with_waybill}")
    assert refused.status_code == 400 and "є ТТН" in refused.json()["detail"]
    assert admin.get(f"/orders/{with_waybill}").status_code == 200
    assert admin.delete("/orders/999999").status_code == 404


def test_shipping_cost_by_the_nova_poshta_tariff(api, db):
    client = api(MANAGER)
    execute(db, "UPDATE dd_product SET weight = 700, length = 30, width = 20, height = 5 WHERE product_id = %s",
            CATEGORY_PRODUCT_ID)  # fmt: skip
    body = {"city": "Київ", "shippingCode": NOVA_POSHTA, "lines": [{"productId": CATEGORY_PRODUCT_ID, "quantity": 2}],
            "cost": 1800}  # fmt: skip

    # The test stub's tariff: 70 per kg begun (1.4 kg → 2), 0.5% of the declared value.
    assert client.post("/orders/new/shipping-cost", json=body).json() == {"cost": 149}
    door = body | {"shippingCode": "novaposhta_address.novaposhta_address"}
    assert client.post("/orders/new/shipping-cost", json=door).json() == {"cost": 179}
    assert client.post("/orders/new/shipping-cost", json=body | {"city": "Атлантида"}).status_code == 400
    assert client.post("/orders/new/shipping-cost", json=body | {"shippingCode": PICKUP}).status_code == 400
    assert client.post("/orders/new/shipping-cost", json=body | {"lines": []}).status_code == 400


def test_web_status_change_asks_to_confirm_a_shipping_problem(api, db):
    client = api(MANAGER)
    order_id = new_order(client, "+380671110017")

    asked = client.post(f"/app/orders/{order_id}/status", data={"status_id": str(SHIPPED)}, headers=HTMX)
    assert "ТТН для цього замовлення не створена" in asked.text and 'name="confirmed"' in asked.text
    assert client.get(f"/orders/{order_id}").json()["statusId"] == PENDING

    confirmed = client.post(
        f"/app/orders/{order_id}/status", data={"status_id": str(SHIPPED), "confirmed": "1"}, headers=HTMX
    )
    assert "Статус змінено." in confirmed.text
    assert client.get(f"/orders/{order_id}").json()["statusId"] == SHIPPED


def test_web_card_tools(api, db):
    client = api(ADMIN)
    order_id = new_order(client, "+380671110018")
    card = client.get(f"/app/orders/{order_id}")
    assert "Надати номер рахунку" in card.text and "Видалити замовлення" in card.text
    assert "Видалити замовлення" not in api(MANAGER).get(f"/app/orders/{order_id}").text

    form = client.get(f"/app/orders/{order_id}/customer/edit", headers=HTMX)
    assert 'name="firstname"' in form.text
    fields = {"firstname": "Ольга", "lastname": "Змінена", "telephone": "+380671110018", "shipping_code": NOVA_POSHTA,
              "payment_code": "cod", "city": "Київ"}  # fmt: skip
    saved = client.post(f"/app/orders/{order_id}/customer", data=fields, headers=HTMX)
    assert "Дані покупця збережено." in saved.text and saved.headers["HX-Retarget"] == "#customer"
    refused = client.post(f"/app/orders/{order_id}/customer", data=fields | {"payment_code": "nope"}, headers=HTMX)
    assert "Необхідний спосіб оплати." in refused.text

    dated = client.post(f"/app/orders/{order_id}/shipping-date", data={"date_shipping": "2026-12-01"}, headers=HTMX)
    assert 'value="2026-12-01"' in dated.text
    assert "Рахунок:" in client.post(f"/app/orders/{order_id}/invoice", headers=HTMX).text
    assert "Бонусні бали" in client.get(f"/app/orders/{order_id}/credits", headers=HTMX).text
    assert client.get("/app/orders/discrepancies", headers=HTMX).text.count("<h4>") == 3

    assert api(MANAGER).post(f"/app/orders/{order_id}/delete", headers=HTMX).status_code == 403
    deleted = client.post(f"/app/orders/{order_id}/delete", headers=HTMX)
    assert deleted.status_code == 204 and deleted.headers["HX-Redirect"] == "/app/orders"
