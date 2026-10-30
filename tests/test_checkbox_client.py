"""The real Checkbox client without a network: httpx.MockTransport records requests and answers like the API."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from newerp.config import CheckboxCashier, CheckboxSettings
from newerp.domain.cashbox import FiscalReceipt, ReceiptSourceLine
from newerp.domain.common import DomainError
from newerp.infrastructure.cashbox.checkbox import HttpCheckboxClient

SETTINGS = CheckboxSettings(
    base_url="https://checkbox.test/",
    cashiers=[CheckboxCashier(cashier_id=1, login="olena", password="pin", license_key="license-1")],
)
SIGN_IN = '{"access_token": "token-1"}'


class FakeApi:
    def __init__(self, *responses: tuple[int, str]) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self._responses.pop(0)
        return httpx.Response(status, text=body)

    def client(self) -> HttpCheckboxClient:
        return HttpCheckboxClient(SETTINGS, httpx.MockTransport(self), close_poll_delay=0)

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def test_sell_signs_in_once_and_sends_the_receipt_in_kopecks():
    api = FakeApi((200, SIGN_IN), (201, '{"id": "r-1"}'), (201, '{"id": "r-2"}'))
    client = api.client()
    receipt = FiscalReceipt.build(
        42, "Олена", [ReceiptSourceLine(101, "Сукня", Decimal(750), 2)], Decimal(1200), "cod", "", "a@b.ua"
    )

    async def run():
        return await client.sell(1, receipt), await client.sell(1, receipt)

    assert asyncio.run(run()) == ("r-1", "r-2")
    assert api.paths == ["/api/v1/cashier/signin", "/api/v1/receipts/sell", "/api/v1/receipts/sell"]
    assert api.requests[1].headers["Authorization"] == "Bearer token-1"
    assert api.requests[1].headers["X-Client-Name"] == "NewErp"
    body = json.loads(api.requests[1].content)
    good = body["goods"][0]
    assert (good["good"]["price"], good["quantity"]) == (75000, 2000)
    assert (body["payments"][0]["type"], body["payments"][0]["value"]) == ("CASHLESS", 120000)
    assert body["discounts"][0]["value"] == 30000
    assert body["delivery"]["email"] == "a@b.ua"


def test_expired_token_signs_in_again_and_repeats():
    api = FakeApi(
        (200, SIGN_IN), (401, '{"message": "Invalid token"}'), (200, '{"access_token": "token-2"}'),
        (201, '{"id": "x-1"}'),
    )  # fmt: skip

    assert asyncio.run(api.client().create_x_report(1)) == "x-1"
    assert api.requests[3].headers["Authorization"] == "Bearer token-2"


def test_open_shift_takes_an_open_one_otherwise_creates_with_the_license_key():
    open_api = FakeApi((200, SIGN_IN), (200, '{"id": "s-open", "status": "OPENED"}'))
    assert asyncio.run(open_api.client().open_shift(1)) == "s-open"

    none = FakeApi((200, SIGN_IN), (200, "null"), (202, '{"id": "s-new", "status": "CREATED"}'))
    assert asyncio.run(none.client().open_shift(1)) == "s-new"
    assert none.paths[2] == "/api/v1/shifts"
    assert none.requests[2].headers["X-License-Key"] == "license-1"


def test_close_shift_waits_for_the_z_report():
    api = FakeApi(
        (200, SIGN_IN),
        (202, '{"id": "s-1", "status": "CLOSING", "z_report": null}'),
        (200, '{"id": "s-1", "status": "CLOSING", "z_report": null}'),
        (200, '{"id": "s-1", "status": "CLOSED", "z_report": {"id": "z-1"}}'),
    )

    assert asyncio.run(api.client().close_shift(1)) == "z-1"
    assert api.paths[3] == "/api/v1/shifts/s-1"


def test_refusal_becomes_domain_error_with_the_checkbox_message():
    api = FakeApi((200, SIGN_IN), (400, '{"message": "Зміну не відкрито"}'))

    with pytest.raises(DomainError, match="Зміну не відкрито"):
        asyncio.run(api.client().create_x_report(1))


def test_unknown_cashier_cannot_be_used_and_receipt_url_has_no_secrets():
    client = FakeApi().client()

    assert not client.can_use(2)
    assert client.receipt_url("r-1") == "https://checkbox.test/api/v1/receipts/r-1/html"
