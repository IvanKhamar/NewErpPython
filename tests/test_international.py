from tests.seed import MANAGER, ORDER_ID, STOREKEEPER

CREATE = {
    "recipient": {
        "firstName": "Anna", "lastName": "Kowalska", "phone": "+48 600 100 200", "email": "anna@example.com",
        "countryCode": "PL", "city": "Kraków", "street": "Floriańska", "building": "5", "postCode": "31-019",
    },
    "items": [{"name": "Сукня лляна", "nameEng": "Linen dress", "hsCode": "6204.49.10", "weightGrams": 450,
               "amount": 1, "cost": 1500}],
    "parcels": [{"weight": 1, "width": 31, "length": 36, "height": 6}],
    "declaredCost": 1500,
    "currency": "UAH",
}  # fmt: skip


def test_form_countries_hs_codes_create_skipped_by_tracking_delete_and_refusals(api):
    manager = api(MANAGER)
    url = f"/orders/{ORDER_ID}/shipment"

    form = manager.get(f"{url}/international/form").json()
    assert (form["recipient"]["firstName"], form["canCreate"], form["test"]) == ("Тарас", True, True)
    assert form["terms"]["incoterm"] == "DAP"
    assert manager.get("/orders/999/shipment/international/form").status_code == 404

    countries = manager.get("/delivery/international/countries").json()
    assert {"code": "PL", "name": "Польща"} in countries
    assert "UA" not in [c["code"] for c in countries]
    assert manager.get("/delivery/international/hs-codes?search=су&country=PL").json()[0]["hsCode"] == "6204491000"
    assert manager.get("/delivery/international/hs-codes?search=с").json() == []

    created = manager.post(f"{url}/international", json=CREATE)
    assert created.status_code == 200, created.text
    shipment = created.json()
    assert (shipment["orderId"], shipment["carrier"], shipment["cost"]) == (ORDER_ID, "international", 1500)
    assert shipment["number"].startswith("TEST")
    assert manager.get(url).json()["carrier"] == "international"

    assert manager.post(f"{url}/international", json=CREATE).status_code == 400  # one per order
    assert manager.delete(url).status_code == 204
    assert manager.get(url).status_code == 204

    incomplete = manager.post(
        f"{url}/international",
        json={"recipient": {"countryCode": "UA"}, "items": [], "parcels": []},
    )
    assert incomplete.status_code == 400
    assert incomplete.json()["detail"].startswith("Не заповнені або некоректні поля: Прізвище")
    assert manager.post("/orders/999/shipment/international", json=CREATE).status_code == 404
    assert api(STOREKEEPER).post(f"{url}/international", json=CREATE).status_code == 403
