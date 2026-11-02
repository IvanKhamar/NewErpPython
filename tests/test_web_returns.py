"""Return and customer pages. Its own database: the test adds the return lookups and a customer."""

from tests.seed import CANCELLED_ORDER_ID, LANGUAGE_ID, MANAGER, ORDER_ID, PRODUCT_ID, STOREKEEPER, insert

HTMX = {"HX-Request": "true"}
WAITING, RECEIVED, WRONG_SIZE, REFUNDED = 1, 2, 1, 1
CUSTOMER_ID = 1


def test_return_is_created_from_the_order_card_and_gets_a_new_status(api, db):
    for status_id, name in ((WAITING, "Очікує"), (RECEIVED, "Отримано")):
        insert(db, "dd_return_status", return_status_id=status_id, language_id=LANGUAGE_ID, name=name)
    insert(db, "dd_return_reason", return_reason_id=WRONG_SIZE, language_id=LANGUAGE_ID, name="Не підійшов розмір")
    insert(db, "dd_return_action", return_action_id=REFUNDED, language_id=LANGUAGE_ID, name="Повернено кошти")
    client = api(MANAGER)

    assert 'hx-get="/app/returns/new?order=' in client.get(f"/app/orders/{ORDER_ID}").text
    form_html = client.get(f"/app/returns/new?order={ORDER_ID}", headers=HTMX).text
    assert f'value="{PRODUCT_ID}"' in form_html
    assert "Не підійшов розмір" in form_html

    form = {"order_id": str(ORDER_ID), "product_id": str(PRODUCT_ID), "quantity": "1", "opened": "1",
            "reason_id": str(WRONG_SIZE), "status_id": str(WAITING), "action_id": "", "comment": "Мала"}  # fmt: skip
    too_many = client.post("/app/returns/new", data=form | {"quantity": "3"}, headers=HTMX)
    assert 'role="alert"' in too_many.text
    assert "HX-Redirect" not in too_many.headers
    created = client.post("/app/returns/new", data=form, headers=HTMX)
    url = created.headers["HX-Redirect"]
    return_id = int(url.rsplit("/", 1)[1])

    card = client.get(url).text
    assert "розпакований" in card
    assert "Мала" in card
    change = {"status_id": str(RECEIVED), "action_id": str(REFUNDED), "comment": "Посилку отримали"}
    saved = client.post(f"{url}/history", data=change, headers=HTMX)
    assert "Статус змінено." in saved.text
    details = client.get(f"/returns/{return_id}").json()
    assert (details["statusName"], details["actionName"]) == ("Отримано", "Повернено кошти")
    assert "Отримано" in client.post(f"{url}/history", data={"status_id": "99"}).text  # refused, the card stays

    listed = client.get(f"/app/returns?status={RECEIVED}", headers=HTMX).text
    assert f'href="/app/returns/{return_id}"' in listed
    assert client.get("/app/returns/9999").status_code == 404
    assert api(STOREKEEPER).get("/app/returns").status_code == 403


def test_customer_list_card_and_notes(api, db):
    insert(
        db, "dd_customer", customer_id=CUSTOMER_ID, customer_group_id=1, language_id=LANGUAGE_ID,
        firstname="Олена", lastname="Коваль", email="olena@example.com", telephone="0935554433", fax="", password="",
        salt="", custom_field="", ip="", status=True, safe=False, token="", code="", address_id=0,
        date_added="2024-05-01",
    )  # fmt: skip
    client = api(MANAGER)

    found = client.get("/app/customers?search=Коваль", headers=HTMX).text
    assert f'href="/app/customers/{CUSTOMER_ID}"' in found
    card = client.get(f"/app/customers/{CUSTOMER_ID}").text
    assert f'href="/app/orders/{CANCELLED_ORDER_ID}"' in card  # a guest order with the same phone

    added = client.post(f"/app/customers/{CUSTOMER_ID}/notes", data={"comment": " Дзвонити ввечері "}, headers=HTMX)
    assert "Нотатку додано." in added.text
    assert "Дзвонити ввечері" in added.text
    assert 'role="alert"' in client.post(f"/app/customers/{CUSTOMER_ID}/notes", data={"comment": " "}).text
    assert client.get("/app/customers/9999").status_code == 404
