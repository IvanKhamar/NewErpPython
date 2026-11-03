"""Customer administration (items 53, 54): edit and addresses, balance and reward points, IPs and unlocking,
logging in as the customer, groups, approvals and custom fields."""

import asyncio
from datetime import datetime, timedelta

import pytest

from newerp.application.customer_admin import LOGIN_TOKEN_PREFIX, CustomerAdminService
from newerp.config import get_settings
from tests.seed import ADMIN, LANGUAGE_ID, MANAGER, insert

CUSTOMER_ID, OTHER_ID, WAITING_ID = 40, 41, 42
RETAIL, WHOLESALE = 1, 2
HTMX = {"HX-Request": "true"}


def customer(db, customer_id: int, email: str, status: bool = True) -> None:
    insert(
        db, "dd_customer", customer_id=customer_id, customer_group_id=RETAIL, language_id=LANGUAGE_ID,
        firstname="Олена", lastname=f"Клієнт{customer_id}", email=email, telephone="0935554433", fax="", password="",
        salt="", custom_field="", ip="", status=status, safe=False, token="", code="", address_id=0,
        date_added="2024-05-01",
    )  # fmt: skip


@pytest.fixture(scope="module", autouse=True)
def customers(db):
    for group_id, name in ((RETAIL, "Роздріб"), (WHOLESALE, "Опт")):
        insert(db, "dd_customer_group", customer_group_id=group_id, approval=0, sort_order=group_id)
        insert(db, "dd_customer_group_description", customer_group_id=group_id, language_id=LANGUAGE_ID, name=name,
               description="")  # fmt: skip
    customer(db, CUSTOMER_ID, "olena@example.com")
    customer(db, OTHER_ID, "other@example.com")
    customer(db, WAITING_ID, "waiting@example.com", status=False)
    insert(db, "dd_customer_approval", customer_id=WAITING_ID, type="customer", date_added="2026-11-01")
    insert(db, "dd_address", address_id=70, customer_id=CUSTOMER_ID, firstname="Олена", lastname="Клієнт", company="",
           address_1="Відділення №1", address_2="", city="Київ", postcode="", custom_field="")  # fmt: skip
    for customer_id in (CUSTOMER_ID, OTHER_ID):
        insert(db, "dd_customer_ip", customer_id=customer_id, ip="10.0.0.7", date_added="2026-10-01")
    insert(db, "dd_customer_login", email="other@example.com", ip="10.0.0.7", total=5, date_added="2026-11-01",
           date_modified="2026-11-01")  # fmt: skip


def test_edit_customer_and_addresses(api):
    client = api(MANAGER)
    form = client.get(f"/customers/{CUSTOMER_ID}/edit").json()
    assert ([a["city"] for a in form["addresses"]], form["customerGroupId"]) == (["Київ"], RETAIL)

    changed = form | {
        "lastname": "Нова", "email": " Olena.New@Example.com ", "customerGroupId": WHOLESALE,
        "addresses": [form["addresses"][0] | {"city": "Львів"},
                      {"city": "Ковель", "address1": "вул. Нова", "default": True}],
    }  # fmt: skip
    saved = client.put(f"/customers/{CUSTOMER_ID}", json=changed)
    assert saved.status_code == 200, saved.text
    saved = saved.json()
    assert (saved["email"], saved["customerGroupId"]) == ("olena.new@example.com", WHOLESALE)
    assert [(a["city"], a["default"]) for a in saved["addresses"]] == [("Львів", False), ("Ковель", True)]

    assert client.put(f"/customers/{CUSTOMER_ID}", json=saved | {"email": "other@example.com"}).status_code == 409
    assert client.put(f"/customers/{CUSTOMER_ID}", json=saved | {"email": "not-an-email"}).status_code == 400
    assert client.put(f"/customers/{CUSTOMER_ID}", json=saved | {"customerGroupId": 99}).status_code == 400
    foreign = saved | {"addresses": [{"id": 99999, "city": "X"}]}
    assert client.put(f"/customers/{CUSTOMER_ID}", json=foreign).status_code == 400
    assert client.get("/customers/9999/edit").status_code == 404


def test_balance_reward_points_ips_and_unlock(api):
    client = api(MANAGER)
    other = client.get(f"/customers/{OTHER_ID}/account").json()
    assert (other["balance"], other["failedLogins"]) == (0, 5)
    assert other["ips"] == [{"ip": "10.0.0.7", "dateAdded": "2026-10-01T00:00:00", "customers": 2}]
    assert client.post(f"/customers/{OTHER_ID}/unlock").json() == {"removedAttempts": 1}
    assert client.get(f"/customers/{OTHER_ID}/account").json()["failedLogins"] == 0

    client.post(f"/customers/{CUSTOMER_ID}/transactions", json={"description": "Повернення", "amount": 300})
    account = client.post(
        f"/customers/{CUSTOMER_ID}/transactions", json={"description": "Оплата", "amount": -120}
    ).json()
    assert (account["balance"], len(account["transactions"])) == (180, 2)
    account = client.post(f"/customers/{CUSTOMER_ID}/rewards", json={"description": "Акція", "points": 50}).json()
    assert account["rewardPoints"] == 50
    assert client.post(f"/customers/{CUSTOMER_ID}/rewards", json={"description": "x", "points": 0}).status_code == 400
    assert (
        client.post(f"/customers/{CUSTOMER_ID}/transactions", json={"description": "", "amount": 5}).status_code == 400
    )


def test_login_link_is_for_the_admin_one_time_and_expires(api, db, monkeypatch):
    # Without a store address there is no link.
    assert api(ADMIN).post(f"/customers/{OTHER_ID}/login-link").status_code == 400
    monkeypatch.setattr(get_settings().customers, "store_url", "https://shop.example/")
    assert api(MANAGER).post(f"/customers/{OTHER_ID}/login-link").status_code == 403
    link = api(ADMIN).post(f"/customers/{OTHER_ID}/login-link").json()
    assert link["url"].startswith("https://shop.example/index.php?route=account/login&token=erp-")
    token = link["url"].split("token=", 1)[1]
    with db.cursor() as cursor:
        cursor.execute("SELECT token FROM dd_customer WHERE customer_id = %s", (OTHER_ID,))
        assert cursor.fetchone()[0] == token


class FakeStore:
    def __init__(self, tokens):
        self.tokens = dict(tokens)

    async def expired_login_tokens(self):
        return list(self.tokens.items())

    async def clear_login_token(self, customer_id, token):
        if self.tokens.get(customer_id) == token:
            del self.tokens[customer_id]


class FixedClock:
    def __init__(self, now):
        self._now = now

    def now(self):
        return self._now


def test_expired_links_are_removed_fresh_ones_stay():
    now = datetime(2026, 11, 3, 12, 0)
    old = f"{LOGIN_TOKEN_PREFIX}{int((now - timedelta(minutes=6)).timestamp())}-a"
    fresh = f"{LOGIN_TOKEN_PREFIX}{int((now - timedelta(minutes=2)).timestamp())}-b"
    store = FakeStore({1: old, 2: fresh, 3: f"{LOGIN_TOKEN_PREFIX}broken"})
    service = CustomerAdminService(store, FixedClock(now), "https://shop.example/", 5)
    assert asyncio.run(service.expire_login_links()) == 2
    assert store.tokens == {2: fresh}


def test_groups_approvals_and_custom_fields(api, db):
    admin, manager = api(ADMIN), api(MANAGER)
    group = admin.post("/customers/groups", json={"name": "VIP", "approval": True, "sortOrder": 3})
    assert group.status_code == 201
    group = group.json()
    assert manager.post("/customers/groups", json={"name": "X"}).status_code == 403
    assert admin.put(f"/customers/groups/{group['id']}", json=group | {"name": "VIP+"}).json()["name"] == "VIP+"
    assert admin.delete(f"/customers/groups/{WHOLESALE}").status_code == 400  # a customer is in it now
    assert admin.delete(f"/customers/groups/{RETAIL}").status_code == 400  # the default group (or with customers)
    assert admin.delete(f"/customers/groups/{group['id']}").status_code == 204

    [waiting] = manager.get("/customers/approvals").json()
    assert (waiting["customerId"], waiting["type"]) == (WAITING_ID, "customer")
    assert manager.post(f"/customers/approvals/{WAITING_ID}/approve").status_code == 204
    assert manager.get("/customers/approvals").json() == []
    assert manager.post(f"/customers/approvals/{WAITING_ID}/deny").status_code == 404
    with db.cursor() as cursor:
        cursor.execute("SELECT status FROM dd_customer WHERE customer_id = %s", (WAITING_ID,))
        assert cursor.fetchone()[0] == 1

    field = admin.post("/customers/custom-fields", json={
        "name": "Розмір одягу", "type": "select", "values": ["S", "M", " ", "L"], "customerGroupIds": [RETAIL],
        "required": True,
    })  # fmt: skip
    assert field.status_code == 201, field.text
    field = field.json()
    assert (field["values"], field["customerGroupIds"], field["required"]) == (["S", "M", "L"], [RETAIL], True)
    assert admin.post("/customers/custom-fields", json={"name": "X", "type": "radio"}).status_code == 400
    assert admin.post("/customers/custom-fields", json={"name": "X", "validation": "(["}).status_code == 400
    assert admin.post("/customers/custom-fields", json={"name": "X", "customerGroupIds": [99]}).status_code == 400
    assert admin.delete(f"/customers/custom-fields/{field['id']}").status_code == 204
    assert admin.delete(f"/customers/custom-fields/{field['id']}").status_code == 404


def test_web_customer_card_sections_and_settings(api, monkeypatch):
    admin, manager = api(ADMIN), api(MANAGER)
    card = manager.get(f"/app/customers/{OTHER_ID}")
    assert 'hx-get="/app/customers/41/account"' in card.text
    form = manager.get(f"/app/customers/{OTHER_ID}/edit", headers=HTMX)
    assert 'name="email"' in form.text
    data = {
        "firstname": "Ірина",
        "lastname": "Веб",
        "email": "other@example.com",
        "telephone": "0671112233",
        "customer_group_id": str(RETAIL),
        "enabled": "1",
        "address_id": ["0"],
        "address_firstname": ["Ірина"],
        "address_lastname": [""],
        "address_company": [""],
        "address_address_1": ["вул. Тиха, 1"],
        "address_address_2": [""],
        "address_city": ["Рівне"],
        "address_postcode": [""],
        "default_address": "0",
    }
    saved = manager.post(f"/app/customers/{OTHER_ID}/edit", data=data, headers=HTMX)
    assert "Збережено." in saved.text and 'value="Рівне"' in saved.text
    refused = manager.post(f"/app/customers/{OTHER_ID}/edit", data=data | {"email": "bad"}, headers=HTMX)
    assert 'class="error"' in refused.text

    account = manager.post(
        f"/app/customers/{OTHER_ID}/account/transaction",
        data={"description": "Подарунок", "amount": "25"},
        headers=HTMX,
    )
    assert "Збережено." in account.text and "Увійти як покупець" not in account.text
    assert "Увійти як покупець" in admin.get(f"/app/customers/{OTHER_ID}/account", headers=HTMX).text
    monkeypatch.setattr(get_settings().customers, "store_url", "https://shop.example/")
    link = admin.post(f"/app/customers/{OTHER_ID}/login-link", headers=HTMX)
    assert "https://shop.example/index.php?route=account/login&amp;token=erp-" in link.text
    assert manager.post(f"/app/customers/{OTHER_ID}/login-link", headers=HTMX).status_code == 403

    for tab in ("groups", "approvals", "fields"):
        assert manager.get(f"/app/customers/settings?tab={tab}").status_code == 200
    added = admin.post("/app/customers/settings/groups/0/save", data={"name": "Партнери"}, headers=HTMX)
    assert "Збережено." in added.text and 'value="Партнери"' in added.text
    assert manager.post("/app/customers/settings/groups/0/save", data={"name": "X"}, headers=HTMX).status_code == 403
