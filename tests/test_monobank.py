"""monobank payment links (item 55): the gateway without a network, the status check, the API and the order page."""

import asyncio
import json
from datetime import datetime

import httpx
import pytest

from newerp.application.monobank import MonobankService, MonoInvoice
from newerp.config import MonobankSettings
from newerp.domain.common import DomainError
from newerp.infrastructure.monobank import HttpMonobankGateway
from tests.seed import CATEGORY_ORDER_ID, MANAGER, STOREKEEPER

HTMX = {"HX-Request": "true"}


def test_gateway_sends_the_token_in_the_header_and_kopecks():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/create"):
            return httpx.Response(200, json={"invoiceId": "inv-1", "pageUrl": "https://pay.mbnk.biz/inv-1"})
        return httpx.Response(
            200,
            json={
                "invoiceId": "inv-1",
                "status": "success",
                "amount": 30050,
                "createdDate": "2026-11-03T10:00:00Z",
                "modifiedDate": "2026-11-03T10:05:00Z",
            },
        )

    gateway = HttpMonobankGateway(MonobankSettings(token="test-token"), httpx.MockTransport(handler))
    created = asyncio.run(gateway.create_invoice(30050, "77", "Оплата замовлення #77", "https://shop.example/"))
    status = asyncio.run(gateway.invoice("inv-1"))

    assert (created.invoice_id, created.page_url) == ("inv-1", "https://pay.mbnk.biz/inv-1")
    body = json.loads(requests[0].content)
    assert (body["amount"], body["ccy"], body["merchantPaymInfo"]["reference"]) == (30050, 980, "77")
    assert requests[0].headers["X-Token"] == "test-token" and "test-token" not in str(requests[0].url)
    assert str(requests[1].url) == "https://api.monobank.ua/api/merchant/invoice/status?invoiceId=inv-1"
    assert (status.status, status.amount) == ("success", 30050)


def test_a_refusal_is_a_domain_error():
    gateway = HttpMonobankGateway(
        MonobankSettings(token="bad"),
        httpx.MockTransport(lambda _: httpx.Response(403, json={"errCode": "403", "errText": "forbidden"})),
    )
    with pytest.raises(DomainError, match="forbidden"):
        asyncio.run(gateway.invoice("inv-1"))


class FakeGateway:
    is_test = False

    def __init__(self, statuses):
        self.statuses = statuses

    async def invoice(self, invoice_id):
        return MonoInvoice(invoice_id, self.statuses[invoice_id], 10000, None, datetime(2026, 11, 3, 11))


class FakeStore:
    def __init__(self):
        self.updated = []

    async def remove_unpaid_duplicates(self, before):
        return 1

    async def unfinished_since(self, since):
        return [("a", "created"), ("b", "created")]

    async def update(self, invoice):
        self.updated.append((invoice.invoice_id, invoice.status))


class Clock:
    def now(self):
        return datetime(2026, 11, 3, 12)


def test_check_saves_only_what_changed():
    store = FakeStore()
    service = MonobankService(None, store, FakeGateway({"a": "success", "b": "created"}), Clock(), "", 3)
    result = asyncio.run(service.check())
    assert (result.checked, result.changed, result.removed_duplicates) == (2, 1, 1)
    assert store.updated == [("a", "success")]


def test_api_makes_test_invoices_without_a_token(api):
    client = api(MANAGER)
    link = client.post(f"/orders/{CATEGORY_ORDER_ID}/mono-link", json={"amount": 300.5})
    assert link.status_code == 200, link.text
    link = link.json()
    assert (link["amount"], link["isTest"]) == (300.5, True)
    assert link["url"].startswith("https://pay.monobank.invalid/test-")

    [invoice] = client.get(f"/orders/{CATEGORY_ORDER_ID}/mono-invoices").json()
    assert (invoice["invoiceId"], invoice["label"], invoice["paid"]) == (link["invoiceId"], "Очікується оплата", False)
    assert invoice["amount"] == 300.5
    assert client.post(f"/orders/{CATEGORY_ORDER_ID}/mono-link", json={"amount": 999999}).status_code == 400
    assert client.post("/orders/999999/mono-link", json={}).status_code == 404
    # The stub has nothing to ask about.
    assert client.post("/payments/monobank/check").json()["clientConfigured"] is False
    assert api(STOREKEEPER).get(f"/orders/{CATEGORY_ORDER_ID}/mono-invoices").status_code == 403


def test_order_page_makes_a_mono_invoice(api):
    client = api(MANAGER)
    page = client.post(f"/app/payments/orders/{CATEGORY_ORDER_ID}/mono-link", data={"amount": ""}, headers=HTMX)
    assert "Рахунок monobank на" in page.text and "тестовий" in page.text
