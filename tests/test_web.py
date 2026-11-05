"""Web pages at /app/: the login, the menu by role, the order pages and their HTMX fragments."""

import re

import pytest

from tests.seed import (
    ACCEPTED,
    ADMIN,
    CANCELLED_ORDER_ID,
    CATEGORY_ORDER_ID,
    CATEGORY_PRODUCT_ID,
    COMPLETED,
    INACTIVE_USER_ID,
    MANAGER,
    MANAGER_USER_ID,
    ORDER_ID,
    PASSWORD,
    PENDING,
    RISKY_PHONE,
    STOREKEEPER,
)

HTMX = {"HX-Request": "true"}  # what HTMX adds to its requests


def test_a_page_without_a_session_sends_to_the_login_form(api):
    response = api().get(f"/app/orders?status={PENDING}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/app/login?next=/app/orders%3Fstatus%3D{PENDING}"

    # HTMX gets a header instead, and the way back is the page the user is on, not the fragment URL.
    current = {**HTMX, "HX-Current-URL": f"http://testserver/app/orders/{ORDER_ID}"}
    response = api().post(f"/app/orders/{ORDER_ID}/status", headers=current)
    assert response.headers["HX-Redirect"] == f"/app/login?next=/app/orders/{ORDER_ID}"


def login_token(client) -> str:
    """The token of the login form; the client keeps its cookie, like a browser."""
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/app/login").text).group(1)


def test_login_needs_the_token_of_its_own_page(api):
    browser, attacker = api(), api()
    form = {"username": MANAGER, "password": PASSWORD, "next": "/app/"}
    assert browser.post("/app/login", data=form).status_code == 400  # no token
    # A token from another browser (an attacker'"'"'s page) does not match this browser'"'"'s cookie.
    refused = browser.post("/app/login", data=form | {"csrf": login_token(attacker)})
    assert refused.status_code == 400
    assert "з іншого сайту" in refused.text


def test_login_sets_the_cookie_and_goes_back(api):
    client = api()
    assert 'name="next" value="/app/orders"' in client.get("/app/login?next=/app/orders").text

    token = login_token(client)
    wrong = client.post("/app/login", data={"username": MANAGER, "password": "wrong", "next": "/app/orders",
                                            "csrf": token})  # fmt: skip
    assert wrong.status_code == 401
    assert "Невірний логін або пароль" in wrong.text

    form = {"username": MANAGER, "password": PASSWORD, "next": "/app/orders", "csrf": login_token(client)}
    response = client.post("/app/login", data=form, follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/app/orders")
    assert client.get("/app/orders", follow_redirects=False).status_code == 200


@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example/app/", "/docs"])
def test_login_never_leaves_the_app(api, target):
    client = api()
    form = {"username": MANAGER, "password": PASSWORD, "next": target, "csrf": login_token(client)}
    response = client.post("/app/login", data=form, follow_redirects=False)
    assert response.headers["location"] == "/app/"


def test_logout_closes_the_session(api):
    client = api(MANAGER)
    response = client.post("/app/logout", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/app/login")
    assert client.get("/app/", follow_redirects=False).status_code == 303


def test_the_menu_shows_only_what_the_role_can_open(api):
    storekeeper = api(STOREKEEPER)
    home = storekeeper.get("/app/")
    assert home.status_code == 200
    assert 'href="/app/orders"' not in home.text
    assert "<summary>Склад</summary>" in home.text
    assert "<summary>Продажі</summary>" not in home.text  # a section with no links for this role is not shown
    assert storekeeper.get("/app/orders").status_code == 403

    manager_home = api(MANAGER).get("/app/")
    assert 'href="/app/orders"' in manager_home.text
    assert "Продажі за 30 днів" in manager_home.text  # the dashboard


def test_the_static_files_are_served(api):
    assert api().get("/app/static/htmx.min.js").status_code == 200


def test_the_order_list_filters_by_status(api):
    client = api(MANAGER)
    page = client.get("/app/orders")
    assert f'href="/app/orders/{ORDER_ID}"' in page.text

    table = client.get(f"/app/orders?status={COMPLETED}", headers=HTMX)
    assert "<html" not in table.text  # HTMX gets only the table
    assert 'href="/app/orders/2"' in table.text
    assert f'href="/app/orders/{ORDER_ID}"' not in table.text


def test_the_status_changes_from_the_card(api, db):
    client = api(MANAGER)
    card = client.get(f"/app/orders/{CATEGORY_ORDER_ID}")
    assert card.status_code == 200
    assert "Сорочка" in card.text

    response = client.post(f"/app/orders/{CATEGORY_ORDER_ID}/status", data={"status_id": ACCEPTED}, headers=HTMX)
    assert response.status_code == 200
    assert "Статус змінено." in response.text
    assert "Марта Тест" in response.text  # the new history entry: who changed it
    with db.cursor() as cursor:
        cursor.execute("SELECT order_status_id FROM dd_order WHERE order_id = %s", (CATEGORY_ORDER_ID,))
        assert cursor.fetchone()[0] == ACCEPTED

    unknown = client.post(f"/app/orders/{CATEGORY_ORDER_ID}/status", data={"status_id": "999"}, headers=HTMX)
    assert 'class="error"' in unknown.text
    assert client.get("/app/orders/999999").status_code == 404


def test_a_manager_is_assigned_from_the_card(api):
    client = api(MANAGER)
    response = client.post(f"/app/orders/{ORDER_ID}/manager", data={"manager_id": MANAGER_USER_ID}, headers=HTMX)
    assert "Менеджера призначено." in response.text

    disabled = client.post(f"/app/orders/{ORDER_ID}/manager", data={"manager_id": INACTIVE_USER_ID}, headers=HTMX)
    assert 'class="error"' in disabled.text


def new_order_form(**changes) -> dict:
    form = {
        "firstname": "Ганна",
        "lastname": "Нова",
        "telephone": "+380501119900",
        "email": "",
        "instagram": "",
        "order_source_id": "1",
        "status_id": str(PENDING),
        "manager_id": "",
        "shipping_code": "novaposhta_warehouse.novaposhta_warehouse",
        "payment_code": "cod",
        "city": "Київ",
        "address2": "Відділення №1",
        # Repeated fields, one per line of the table.
        "product_id": [str(CATEGORY_PRODUCT_ID)],
        "option_id": ["502"],
        "quantity": ["2"],
        "price": ["900,00"],
        "shipping_cost": "0",
        "discount": "100",
        "prepayment": "0",
        "comment": "",
    }
    return form | changes


def test_a_new_order_is_created_from_the_form(api):
    client = api(MANAGER)
    assert "Створити замовлення" in client.get("/app/orders/new").text
    found = client.get("/app/orders/new/products?search=Сорочка", headers=HTMX)
    assert "/app/orders/new/line?" in found.text
    line = client.get(f"/app/orders/new/line?product_id={CATEGORY_PRODUCT_ID}&option_id=502&name=X&price=900")
    assert f'name="product_id" value="{CATEGORY_PRODUCT_ID}"' in line.text

    response = client.post("/app/orders/new", data=new_order_form(), headers=HTMX)
    assert response.status_code == 200, response.text
    assert response.headers["HX-Retarget"] == "#new-order"
    order_id = int(re.search(r"/app/orders/(\d+)", response.text).group(1))

    order = client.get(f"/orders/{order_id}").json()
    assert (order["total"], order["statusId"], order["managerId"]) == (1700, PENDING, MANAGER_USER_ID)


def test_a_wrong_new_order_keeps_the_form(api):
    client = api(MANAGER)
    no_lines = new_order_form(product_id=[], option_id=[], quantity=[], price=[])
    for form in (no_lines, new_order_form(discount="багато")):
        response = client.post("/app/orders/new", data=form, headers=HTMX)
        assert 'class="error"' in response.text
        assert "HX-Retarget" not in response.headers


def test_the_customer_risk_shows_cancelled_orders(api):
    risk = api(MANAGER).get("/app/orders/new/risk", params={"telephone": RISKY_PHONE}, headers=HTMX)
    assert f"/app/orders/{CANCELLED_ORDER_ID}" in risk.text


def test_own_password_is_changed(api):
    client = api(ADMIN)

    def change(current: str, new: str, confirm: str):
        form = {"current_password": current, "new_password": new, "confirm_password": confirm}
        return client.post("/app/password", data=form, follow_redirects=False)

    assert "не збігаються" in change(PASSWORD, "new-password-1", "other").text
    assert "Поточний пароль неправильний." in change("wrong", "new-password-1", "new-password-1").text
    assert change(PASSWORD, "new-password-1", "new-password-1").headers["location"] == "/app/"
    assert client.get("/app/", follow_redirects=False).status_code == 200  # the session was issued again
    assert change("new-password-1", PASSWORD, PASSWORD).status_code == 303
