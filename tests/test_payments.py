from tests.seed import ADMIN, MANAGER, STOREKEEPER

TOTAL, NAME = 2, 16  # MatchReason flags


def test_suggest_link_hide_payment(api):
    client = api(MANAGER)
    [payment] = client.get("/payments/bank?linked=false").json()["items"]
    assert (payment["amount"], payment["balance"], payment["payer"]) == (2000, 5000, "Тарас Клієнт2")

    best = client.get(f"/payments/bank/{payment['id']}/suggestions").json()[0]
    assert best["orderId"] == 2
    assert best["reasons"] == TOTAL | NAME
    assert best["status"] == "5) Відправлено"

    linked = client.put(f"/payments/bank/{payment['id']}/order", json={"orderId": 2})
    assert linked.json()["orderId"] == 2
    [for_order] = client.get("/orders/2/payments").json()
    assert for_order["id"] == payment["id"]
    assert client.put(f"/payments/bank/{payment['id']}/order", json={"orderId": 999}).status_code == 404

    assert client.put(f"/payments/bank/{payment['id']}/visible?visible=false").status_code == 204
    assert client.get("/payments/bank").json()["items"] == []
    assert len(client.get("/payments/bank?hidden=true").json()["items"]) == 1


def test_import_without_bank_keys_reports_not_configured(api):
    result = api(ADMIN).post("/payments/bank/import").json()

    assert result == {"clientConfigured": False, "fetched": 0, "added": 0, "skipped": 0}


def test_liqpay_without_keys_gives_a_test_link_and_not_paid(api):
    client = api(MANAGER)

    link = client.post("/orders/2/liqpay-link", json={"amount": 500}).json()
    assert (link["amount"], link["currency"], link["isTest"]) == (500, "UAH", True)
    assert link["url"].startswith("https://www.liqpay.ua/api/3/checkout?data=")

    status = client.get("/orders/2/liqpay-status").json()
    assert (status["state"], status["label"]) == (0, "Ще не оплачено")  # NotFound
    assert client.post("/orders/2/liqpay-link", json={"amount": 2000.01}).status_code == 400
    assert client.post("/orders/999/liqpay-link", json={}).status_code == 404
    assert api(STOREKEEPER).get("/orders/2/liqpay-status").status_code == 403
