from tests.seed import ADMIN, BRANCH_1_REF, KYIV_REF, MANAGER, ORDER_ID, SHIPPING_ORDER_ID, STOREKEEPER, WAITING_TTN


def test_shipment_shown_for_order_with_waybill_no_content_otherwise(api):
    client = api(MANAGER)

    shipment = client.get("/orders/2/shipment").json()
    assert shipment["number"] == WAITING_TTN
    assert (shipment["statusCode"], shipment["tone"], shipment["isFinal"]) == (7, 0, False)  # Info
    assert client.get(f"/orders/{ORDER_ID}/shipment").status_code == 204


def test_reminders_list_waiting_parcel_until_marked(api):
    client = api(ADMIN)

    [reminder] = client.get("/delivery/reminders").json()
    assert (reminder["orderId"], reminder["number"], reminder["managerName"]) == (2, WAITING_TTN, "Олена Тест")

    assert client.post("/delivery/reminders/2/done").status_code == 204
    assert client.get("/delivery/reminders").json() == []
    assert client.post("/delivery/reminders/999/done").status_code == 404


def test_refresh_without_key_reports_not_configured(api):
    result = api(MANAGER).post("/delivery/tracking/refresh").json()

    assert result == {"clientConfigured": False, "checked": 0, "updated": 0}


def test_order_search_finds_by_waybill(api):
    page = api(ADMIN).get(f"/orders?search={WAITING_TTN}").json()

    assert [o["id"] for o in page["items"]] == [2]


def test_shipment_create_print_delete_with_test_documents(api):
    client = api(MANAGER)
    url = f"/orders/{SHIPPING_ORDER_ID}/shipment"

    form = client.get(f"{url}/form").json()
    assert (form["city"]["ref"], form["warehouse"]["ref"]) == (KYIV_REF, BRANCH_1_REF)
    assert form["cashOnDelivery"] == 1200  # pay on receipt
    assert form["testMode"] is True
    seat = form["seatPresets"][0]["seat"]
    assert seat["volume"] == 0.006696

    request = {
        "firstName": form["firstName"], "lastName": form["lastName"], "middleName": "", "phone": form["phone"],
        "cityRef": form["city"]["ref"], "warehouseRef": form["warehouse"]["ref"],
        "declaredCost": form["declaredCost"], "cashOnDelivery": form["cashOnDelivery"], "senderPays": False,
        "seats": [seat],
    }  # fmt: skip
    created = client.post(url, json=request)
    assert created.status_code == 200, created.text
    number = created.json()["number"]
    assert number.startswith("00")
    assert client.get(url).json()["number"] == number

    assert client.post(url, json=request).status_code == 400  # one waybill per order
    assert client.get(f"{url}/print?kind=Markings").status_code == 400  # no printing without a key
    assert client.delete(url).status_code == 204
    assert client.get(url).status_code == 204


def test_shipment_to_closed_branch_is_rejected(api):
    response = api(MANAGER).post(
        f"/orders/{SHIPPING_ORDER_ID}/shipment",
        json={
            "firstName": "Тарас", "lastName": "Клієнт", "phone": "0670000005", "cityRef": KYIV_REF,
            "warehouseRef": "wh-kyiv-3", "declaredCost": 1200,
            "seats": [{"weight": 1, "width": 30, "length": 30, "height": 10}],
        },
    )  # fmt: skip

    assert response.status_code == 400
    assert response.json()["detail"] == "Такого відділення в цьому місті немає в довіднику."


def test_directory_searches_cities_and_working_branches(api):
    client = api(MANAGER)

    assert client.get("/delivery/cities?search=Ки").json() == [{"ref": KYIV_REF, "name": "Київ"}]
    branches = client.get(f"/delivery/warehouses?cityRef={KYIV_REF}").json()
    assert [b["ref"] for b in branches] == [BRANCH_1_REF, "wh-kyiv-2"]  # the closed №3 is not shown
    assert client.post("/delivery/warehouses/refresh").json() == {"clientConfigured": False, "count": 0}


def test_storekeeper_has_no_access(api):
    assert api(STOREKEEPER).get("/delivery/reminders").status_code == 403
