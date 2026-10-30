from tests.seed import ADMIN, CASHIER_ID, MANAGER, ORDER_ID, STOREKEEPER


def test_shift_cheque_and_z_report_full_day(api):
    client = api(MANAGER)
    setup = client.get("/cashbox/setup").json()
    assert setup["fakeMode"] is True
    assert next(c for c in setup["cashiers"] if c["id"] == CASHIER_ID)["canFiscalize"] is True

    refused = client.post(f"/orders/{ORDER_ID}/cheque", json={})
    assert refused.status_code == 400  # no open shift yet
    assert client.get(f"/orders/{ORDER_ID}/cheque").status_code == 204

    shift = client.post(f"/cashbox/cashiers/{CASHIER_ID}/shift/open").json()
    assert shift["dateEnd"] is None

    issued = client.post(f"/orders/{ORDER_ID}/cheque", json={})
    assert issued.status_code == 200, issued.text
    cheque = issued.json()
    assert (cheque["sum"], cheque["isFiscalized"], cheque["workShiftId"]) == (1500, True, shift["id"])
    assert cheque["customer"] == "Тарас Клієнт1"

    x = client.post(f"/cashbox/cashiers/{CASHIER_ID}/x-report").json()
    assert "ТЕСТОВИЙ" in x["text"]
    [only] = client.get("/cashbox/shifts").json()
    assert (only["cheques"], only["chequesSum"]) == (1, 1500)

    report = client.post(f"/cashbox/cashiers/{CASHIER_ID}/shift/close").json()
    assert report["closedBy"] == "Марта Тест"
    [fiscalized] = client.get("/cashbox/cheques?fiscalized=true").json()["items"]
    assert fiscalized["reportId"] == report["id"]
    assert client.get("/cashbox/shifts").json()[0]["dateEnd"] is not None
    assert client.delete(f"/cashbox/cheques/{cheque['id']}").status_code == 400


def test_setup_edits_only_for_admin_and_warehouse_in_use_is_kept(api):
    manager, admin = api(MANAGER), api(ADMIN)
    assert manager.post("/cashbox/warehouses", json={"name": "Склад"}).status_code == 403

    created = admin.post("/cashbox/warehouses", json={"name": "Магазин на Подолі", "description": "вул. Сагайдачного"})
    warehouse_id = created.json()["id"]
    cashier = admin.post(
        "/cashbox/cashiers",
        json={"name": "Ігор", "paymentCode": "cod", "autoCloseTime": "21:30:00", "warehouseId": warehouse_id,
              "requisitesId": 0, "isActive": True},
    )  # fmt: skip
    assert cashier.status_code == 200, cashier.text
    assert admin.delete(f"/cashbox/warehouses/{warehouse_id}").status_code == 400

    igor = next(c for c in admin.get("/cashbox/setup").json()["cashiers"] if c["name"] == "Ігор")
    assert (igor["warehouse"], igor["autoCloseTime"]) == ("Магазин на Подолі", "21:30:00")
    assert api(STOREKEEPER).get("/cashbox/setup").status_code == 403
