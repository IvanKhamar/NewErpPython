"""The PrivatBank client and the LiqPay gateway without a network (httpx.MockTransport)."""

import asyncio
import base64
import hashlib
import json
from datetime import UTC, date, datetime, time
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from newerp.application.payments import LiqPayCheckout
from newerp.config import LiqPaySettings, PrivatBankAccount, PrivatBankSettings
from newerp.domain.common import DomainError
from newerp.infrastructure.payments import HttpLiqPayGateway, HttpPrivatBankClient, liqpay_gateway, parse_statement

STATEMENT = """{"StatementsResponse": {"statements": [
  {"UA213052990000026001000000001": [
    {"0": {"ID": "tx-1", "TRANTYPE": "C", "BPL_SUM_E": "1500.00", "BPL_DAT_KL": "15.10.2026", "BPL_TIM_P": "14:05",
           "BPL_OSND": "Оплата зам. 2045", "BPL_A_NAM": "Іваненко Ірина", "BPL_A_ACC": "UA11", "BPL_A_MFO": "305299",
           "BPL_A_CRF": "1234567890", "BPL_C_BAL": "9000.50"}},
    {"1": {"ID": "tx-2", "TRANTYPE": "D", "BPL_SUM_E": "200", "BPL_DAT_KL": "15.10.2026", "BPL_TIM_P": "15:00",
           "BPL_OSND": "Комісія"}}
  ]}
]}}"""
LIQPAY = LiqPaySettings(public_key="pub-test", private_key="priv-test")


def signature(data: str) -> str:
    return base64.b64encode(hashlib.sha1(f"priv-test{data}priv-test".encode()).digest()).decode()


def test_statement_reads_incoming_and_debit():
    first, second = parse_statement(STATEMENT)

    assert (first.id, first.account, first.is_incoming) == ("tx-1", "UA213052990000026001000000001", True)
    assert (first.amount, first.balance) == (Decimal(1500), Decimal("9000.50"))
    assert (first.date, first.time) == (date(2026, 10, 15), time(14, 5))
    assert (first.purpose, first.payer, first.payer_bank_code) == ("Оплата зам. 2045", "Іваненко Ірина", "305299")
    assert not second.is_incoming


def test_fetch_sends_id_and_token_headers_and_decodes_windows_1251():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=STATEMENT.encode("cp1251"))

    settings = PrivatBankSettings(accounts=[PrivatBankAccount(id="client-id", token="secret")])
    client = HttpPrivatBankClient(settings, httpx.MockTransport(handler))

    transactions = asyncio.run(client.fetch(date(2026, 10, 15), date(2026, 10, 16)))

    assert transactions[0].purpose == "Оплата зам. 2045"
    url = requests[0].url
    assert str(url) == "https://acp.privatbank.ua/api/proxy/transactions?startDate=15-10-2026&endDate=16-10-2026"
    assert (requests[0].headers["id"], requests[0].headers["token"]) == ("client-id", "secret")


def test_unknown_statement_format_is_a_domain_error():
    with pytest.raises(DomainError):
        parse_statement('{"error": "Unauthorized"}')


def test_checkout_url_has_signed_base64_data():
    url = HttpLiqPayGateway(LIQPAY).checkout_url(LiqPayCheckout("2045", Decimal("300.5"), "UAH", "Оплата #2045."))

    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://www.liqpay.ua/api/3/checkout"
    query = parse_qs(parts.query)
    data = query["data"][0]
    payload = json.loads(base64.b64decode(data))
    assert (payload["public_key"], payload["action"], payload["version"]) == ("pub-test", "pay", "3")
    assert (payload["order_id"], payload["amount"], payload["description"]) == ("2045", 300.5, "Оплата #2045.")
    assert query["signature"][0] == signature(data)


def test_status_posts_a_signed_request_and_parses_a_paid_reply():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text='{"result":"ok","status":"success","amount":300.5,"end_date":1792483200000}')

    reply = asyncio.run(HttpLiqPayGateway(LIQPAY, httpx.MockTransport(handler)).status("2045"))

    assert (reply.status, reply.error_code, reply.amount) == ("success", None, Decimal("300.5"))
    assert reply.end_date == datetime.fromtimestamp(1792483200, UTC)
    assert str(requests[0].url) == "https://www.liqpay.ua/api/request"
    form = parse_qs(requests[0].content.decode())
    request = json.loads(base64.b64decode(form["data"][0]))
    assert (request["action"], request["order_id"]) == ("status", "2045")
    assert form["signature"][0] == signature(form["data"][0])


def test_without_keys_the_gateway_is_a_test_one_and_never_paid():
    gateway = liqpay_gateway(LiqPaySettings())

    assert gateway.is_test
    assert gateway.checkout_url(LiqPayCheckout("1", Decimal(10), "UAH", "x")).startswith(
        "https://www.liqpay.ua/api/3/checkout?data="
    )
    assert asyncio.run(gateway.status("1")).error_code == "payment_not_found"
