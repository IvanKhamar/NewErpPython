"""Its own test database: these tests change roles and access of the seeded users."""

from tests.seed import ACTIVE_USER_ID, ADMIN, MANAGER, MANAGER_USER_ID, PASSWORD, STOREKEEPER, STOREKEEPER_USER_ID


def test_new_worker_logs_in_with_temporary_password_changes_it_stays_logged_in(api):
    admin = api(ADMIN)
    created = admin.post(
        "/users",
        json={
            "username": "novyi",
            "firstName": "Новий",
            "lastName": "Працівник",
            "email": "new@example.com",
            "role": "Manager",
        },
    )
    assert created.status_code == 201
    assert created.headers["Location"] == f"/users/{created.json()['userId']}"
    temporary = created.json()["temporaryPassword"]

    worker = api("novyi", temporary)
    assert worker.get("/auth/me").json()["mustChangePassword"] is True

    change = worker.post("/auth/change-password", json={"currentPassword": temporary, "newPassword": "my-own-password"})
    assert change.status_code == 204
    # The session was issued again: the person stays logged in, without the temporary-password mark.
    assert worker.get("/auth/me").json()["mustChangePassword"] is False

    users = admin.get("/users").json()
    assert any(u["username"] == "novyi" and u["hasAccess"] and u["role"] == "Manager" for u in users)


def test_role_change_and_revoke_end_open_sessions_immediately(api):
    admin = api(ADMIN)
    manager = api(MANAGER)
    assert manager.get("/auth/me").status_code == 200

    assert admin.put(f"/users/{MANAGER_USER_ID}/access", json={"role": "Storekeeper"}).status_code == 200
    assert manager.get("/auth/me").status_code == 401  # the old cookie had the Manager role

    storekeeper = api(MANAGER)  # same password, new role
    assert storekeeper.get("/auth/me").json()["role"] == "Storekeeper"

    assert admin.delete(f"/users/{MANAGER_USER_ID}/access").status_code == 204
    assert storekeeper.get("/auth/me").status_code == 401
    assert api().post("/auth/login", json={"username": MANAGER, "password": PASSWORD}).status_code == 401


def test_reset_password_old_password_stops_working(api):
    admin = api(ADMIN)
    reset = admin.post(f"/users/{STOREKEEPER_USER_ID}/reset-password", json={"temporaryPassword": "temporary-123"})
    assert reset.status_code == 200

    assert api().post("/auth/login", json={"username": STOREKEEPER, "password": PASSWORD}).status_code == 401
    new_login = api().post("/auth/login", json={"username": STOREKEEPER, "password": "temporary-123"})
    assert new_login.json()["mustChangePassword"] is True


def test_guards_admin_cannot_revoke_self_others_cannot_manage_users(api):
    admin = api(ADMIN)
    response = admin.delete(f"/users/{ACTIVE_USER_ID}/access")
    assert response.status_code == 400
    assert response.json()["title"] == "Порушено бізнес-правило"

    created = admin.post(
        "/users",
        json={
            "username": "guard-sklad",
            "firstName": "Комірник",
            "lastName": "Тест",
            "role": "Storekeeper",
            "temporaryPassword": "guard-password",
        },
    )
    assert created.status_code == 201
    assert api("guard-sklad", "guard-password").get("/users").status_code == 403


def test_unknown_role_is_a_business_rule_error(api):
    response = api(ADMIN).post("/users", json={"username": "x-user", "firstName": "X", "lastName": "Y", "role": "Boss"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Роль має бути однією з: Admin, Manager, Storekeeper."
