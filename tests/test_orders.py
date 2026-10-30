from tests.seed import (
    ACTIVE_USER_ID,
    ADMIN,
    COMPLETED,
    INACTIVE_USER_ID,
    MANAGER,
    MANAGER_USER_ID,
    ORDER_ID,
    PENDING,
    STOREKEEPER,
)


def test_list_hides_unfinished_orders_and_joins_lookups(api):
    page = api(ADMIN).get("/orders?from=2026-10-01&to=2026-10-31").json()

    assert 3 not in [o["id"] for o in page["items"]]
    completed = next(o for o in page["items"] if o["id"] == 2)
    assert completed["statusName"] == "5) Відправлено"
    assert completed["sourceName"] == "Instagram"
    assert completed["managerName"] == "Олена Тест"
    assert page["totalPages"] == 1


def test_list_filters_by_manager(api):
    page = api(ADMIN).get(f"/orders?managerId={ACTIVE_USER_ID}").json()

    assert [o["id"] for o in page["items"]] == [2]


def test_list_searches_by_phone(api):
    page = api(ADMIN).get("/orders?search=0000002").json()

    assert [o["id"] for o in page["items"]] == [2]


def test_change_status_updates_order_and_writes_history(api):
    client = api(ADMIN)

    response = client.put(f"/orders/{ORDER_ID}/status", json={"statusId": COMPLETED})
    assert response.status_code == 200
    assert response.json()["warnings"] == []

    order = client.get(f"/orders/{ORDER_ID}").json()
    assert order["statusId"] == COMPLETED
    [entry] = order["statusHistory"]
    assert (entry["oldStatusId"], entry["newStatusId"]) == (PENDING, COMPLETED)
    assert entry["userId"] == ACTIVE_USER_ID  # who changed it, the server took from the session


def test_change_status_to_the_same_status_is_rejected(api):
    response = api(ADMIN).put("/orders/2/status", json={"statusId": COMPLETED})

    assert response.status_code == 400
    assert response.json()["detail"] == "Замовлення вже має цей статус."


def test_get_unknown_order_returns_404(api):
    assert api(ADMIN).get("/orders/12345").status_code == 404


def test_managers_lists_only_active_users_and_is_open_to_managers(api):
    managers = api(MANAGER).get("/orders/managers").json()

    assert {"id": MANAGER_USER_ID, "name": "Марта Тест"} in managers
    assert INACTIVE_USER_ID not in [m["id"] for m in managers]


def test_assign_manager(api):
    client = api(ADMIN)

    assert client.put(f"/orders/{ORDER_ID}/manager", json={"managerId": MANAGER_USER_ID}).status_code == 204
    assert client.get(f"/orders/{ORDER_ID}").json()["managerName"] == "Марта Тест"
    assert client.put(f"/orders/{ORDER_ID}/manager", json={"managerId": INACTIVE_USER_ID}).status_code == 400


def test_roles_limit_access_to_orders(api):
    assert api(STOREKEEPER).get("/orders").status_code == 403
    assert api(MANAGER).get("/orders").status_code == 200
    assert api().get("/orders").status_code == 401
