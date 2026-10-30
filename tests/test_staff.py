from tests.seed import ACTIVE_USER_ID, ADMIN, MANAGER


def test_bonuses_count_completed_orders_of_manager(api):
    report = api(ADMIN).get("/staff/bonuses?from=2026-10-01&to=2026-10-31").json()

    [row] = report["managers"]
    assert (row["managerId"], row["managerName"], row["ordersCount"]) == (ACTIVE_USER_ID, "Олена Тест", 1)
    assert row["bonus"] == 200  # 10% of order 2 for 2000
    assert (report["from"], report["to"], report["percent"], report["totalBonus"]) == (
        "2026-10-01",
        "2026-10-31",
        10,
        200,
    )


def test_bonuses_reject_reversed_period(api):
    response = api(ADMIN).get("/staff/bonuses?from=2026-10-31&to=2026-10-01")

    assert response.status_code == 400
    assert response.json()["detail"] == "Дата кінця періоду раніша за дату початку."


def test_only_admin_sees_bonuses(api):
    assert api(MANAGER).get("/staff/bonuses?from=2026-10-01&to=2026-10-31").status_code == 403
