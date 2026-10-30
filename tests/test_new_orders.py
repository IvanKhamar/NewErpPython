from tests.seed import CANCELLED_ORDER_ID, CATEGORY_PRODUCT_ID, MANAGER, MANAGER_USER_ID, PENDING, STOREKEEPER


def request(phone: str, quantity: int = 2) -> dict:
    return {
        "firstname": "Ганна",
        "lastname": "Нова",
        "telephone": phone,
        "email": "",
        "instagram": "@hanna",
        "orderSourceId": 1,
        "statusId": PENDING,
        "managerId": MANAGER_USER_ID,
        "shippingCode": "novaposhta_warehouse.novaposhta_warehouse",
        "shippingMethod": "Нова Пошта",
        "city": "Київ",
        "address1": "",
        "address2": "Відділення №1",
        "house": "",
        "flat": "",
        "paymentCode": "cod",
        "paymentMethod": "Накладений платіж",
        "lines": [{"productId": CATEGORY_PRODUCT_ID, "productOptionValueId": 502, "quantity": quantity, "price": 900}],
        "shippingCost": 0,
        "discount": 100,
        "prepayment": 200,
        "comment": "Дзвінок",
    }


def test_create_writes_order_lines_options_and_status(api):
    client = api(MANAGER)

    response = client.post("/orders", json=request("+380501112233"))
    assert response.status_code == 201
    created = response.json()
    assert created["risk"]["level"] == 0  # New
    assert response.headers["Location"] == f"/orders/{created['orderId']}"

    order = client.get(f"/orders/{created['orderId']}").json()
    assert (order["total"], order["statusId"], order["customer"]) == (1700, PENDING, "Ганна Нова")
    assert (order["sourceName"], order["managerId"]) == ("Instagram", MANAGER_USER_ID)
    [line] = order["products"]
    assert (line["name"], line["model"], line["quantity"], line["total"]) == ("Сорочка лляна оверсайз", "T-2", 2, 1800)
    [history] = order["statusHistory"]
    assert (history["oldStatusId"], history["newStatusId"]) == (0, PENDING)

    # The new order is in the list at once, and the customer in the search.
    [customer] = client.get("/orders/new/customers?search=1112233").json()
    assert (customer["firstname"], customer["instagram"], customer["lastOrderId"]) == (
        "Ганна",
        "@hanna",
        created["orderId"],
    )

    composition = client.get(f"/orders/{created['orderId']}/composition").json()
    assert (composition["discount"], composition["prepayment"], composition["total"]) == (100, 200, 1700)


def test_create_invalid_order_returns_400(api):
    response = api(MANAGER).post("/orders", json=request("+380501112244", quantity=0))

    assert response.status_code == 400
    assert response.json()["detail"] == "Кількість товару має бути більшою за нуль."


def test_product_search_returns_price_and_sizes(api):
    [product] = api(MANAGER).get("/orders/new/products?search=лляна").json()

    assert (product["id"], product["price"]) == (CATEGORY_PRODUCT_ID, 900)
    [size] = product["options"]
    assert (size["productOptionValueId"], size["optionName"], size["value"]) == (502, "Розмір", "M")


def test_customer_risk_finds_cancelled_order_by_phone_in_any_format(api):
    risk = api(MANAGER).get("/orders/customer-risk?telephone=0935554433").json()

    assert (risk["level"], risk["totalOrders"]) == (3, 1)  # Red
    assert risk["cancelledOrderIds"] == [CANCELLED_ORDER_ID]
    assert risk["suspicionRate"] == 100


def test_methods_come_from_existing_orders(api):
    methods = api(MANAGER).get("/orders/new/methods").json()

    assert {"code": "cod", "name": "Накладений платіж"} in methods["payment"]
    assert {"code": "novaposhta_warehouse.novaposhta_warehouse", "name": "Нова Пошта"} in methods["shipping"]


def test_composition_change_replaces_lines_and_totals(api):
    client = api(MANAGER)
    order_id = client.post("/orders", json=request("+380501112266", quantity=1)).json()["orderId"]

    changed = client.put(
        f"/orders/{order_id}/composition",
        json={
            "lines": [{"productId": CATEGORY_PRODUCT_ID, "productOptionValueId": 502, "quantity": 3, "price": 900}],
            "shippingCost": 70,
            "discount": 100,
            "prepayment": 500,
        },
    )
    assert changed.status_code == 200
    assert changed.json() == {"total": 2670, "warnings": []}

    composition = client.get(f"/orders/{order_id}/composition").json()
    [line] = composition["lines"]
    assert (line["quantity"], composition["shippingCost"], composition["discount"]) == (3, 70, 100)
    assert composition["total"] == 2670
    assert client.get(f"/orders/{order_id}").json()["total"] == 2670
    assert client.get("/orders/9999/composition").status_code == 404


def test_storekeeper_cannot_create_orders(api):
    assert api(STOREKEEPER).post("/orders", json=request("+380501112255")).status_code == 403
