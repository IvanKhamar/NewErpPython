"""User and bonus pages. Its own test database: the tests change roles and access of the seeded users."""

import re

from tests.seed import ACTIVE_USER_ID, ADMIN, MANAGER, STOREKEEPER, STOREKEEPER_USER_ID

HTMX = {"HX-Request": "true"}


def temporary_password(html: str) -> str:
    return re.search(r'<article class="temporary-password">.*?<code>(.+?)</code>', html, re.S).group(1)


def test_new_worker_gets_a_temporary_password_shown_once(api):
    admin = api(ADMIN)
    assert 'href="/app/users"' in admin.get("/app/").text
    form = {"username": "veb", "first_name": "Нова", "last_name": "Веб", "email": "veb@example.com", "role": "Manager"}
    created = admin.post("/app/users/new", data=form, headers=HTMX)
    assert "Працівника додано." in created.text
    assert created.headers["Cache-Control"] == "no-store"
    password = temporary_password(created.text)

    worker = api("veb", password)
    assert worker.get("/app/orders", follow_redirects=False).headers["location"] == "/app/password"
    assert "temporary-password" not in admin.get("/app/users").text
    assert 'role="alert"' in admin.post("/app/users/new", data=form, headers=HTMX).text  # the login is taken


def test_role_reset_and_revoke(api):
    admin = api(ADMIN)
    url = f"/app/users/{STOREKEEPER_USER_ID}"
    changed = admin.post(f"{url}/access", data={"role": "Manager"}, headers=HTMX)
    assert "Доступ і роль збережено." in changed.text
    assert next(u for u in admin.get("/users").json() if u["userId"] == STOREKEEPER_USER_ID)["role"] == "Manager"

    reset = admin.post(f"{url}/reset-password", headers=HTMX)
    api(STOREKEEPER, temporary_password(reset.text))  # logs in with the new temporary password
    assert "Доступ забрано" in admin.post(f"{url}/revoke", headers=HTMX).text
    assert 'role="alert"' in admin.post(f"/app/users/{ACTIVE_USER_ID}/revoke", headers=HTMX).text  # not oneself
    assert api(MANAGER).get("/app/users").status_code == 403


def test_bonuses_for_a_period(api):
    admin = api(ADMIN)
    page = admin.get("/app/bonuses?since=2026-10-01&until=2026-10-31").text
    assert "Олена Тест" in page
    assert "Дата: потрібен формат" in admin.get("/app/bonuses?since=жовтень").text
    assert 'role="alert"' in admin.get("/app/bonuses?since=2026-10-31&until=2026-10-01").text
    assert api(MANAGER).get("/app/bonuses").status_code == 403
