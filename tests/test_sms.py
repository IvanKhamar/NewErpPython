import asyncio
import json

import httpx
import pytest

from newerp.config import SmsClubSettings
from newerp.infrastructure.sms import NOT_ALLOWED_RESPONSE, STUB_RESPONSE, SmsClubSender
from tests.seed import ADMIN, MANAGER, ORDER_ID, STOREKEEPER, WAITING_TTN

ACCEPTED = '{"success_request":{"info":{"1092":"380670000001"}}}'


def test_waybill_of_another_order_asks_for_confirmation(api):
    client = api(MANAGER)
    text = f"Ваша ТТН {WAITING_TTN}"

    blocked = client.post(f"/orders/{ORDER_ID}/sms", json={"text": text}).json()
    assert blocked["ttnMismatch"] == {"ttn": WAITING_TTN, "ttnOrderId": 2}
    assert client.get(f"/orders/{ORDER_ID}/sms").json() == []

    forced = client.post(f"/orders/{ORDER_ID}/sms", json={"text": text, "force": True}).json()
    assert forced["message"] is not None


def test_stub_does_not_send_but_logs_to_order_history(api):
    client = api(MANAGER)

    result = client.post("/orders/2/sms", json={"text": "Ваше замовлення прийнято"}).json()

    assert result["sent"] is False
    assert result["message"]["phone"] == "380670000002"  # +380670000002 of the test order
    [entry] = client.get("/orders/2/sms").json()
    assert entry["response"] == STUB_RESPONSE


def test_settings_without_token_not_configured(api):
    assert api(MANAGER).get("/sms/settings").json() == {"isConfigured": False}


def test_empty_text_is_bad_request(api):
    assert api(ADMIN).post(f"/orders/{ORDER_ID}/sms", json={"text": "  "}).status_code == 400


def test_storekeeper_has_no_access(api):
    assert api(STOREKEEPER).post(f"/orders/{ORDER_ID}/sms", json={"text": "Текст"}).status_code == 403


class FakeSmsClub:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, text=self.reply)


def test_sms_club_posts_json_with_bearer_token_and_reads_the_accepted_number():
    api = FakeSmsClub(ACCEPTED)
    sender = SmsClubSender(SmsClubSettings(token="test-token"), httpx.MockTransport(api))

    result = asyncio.run(sender.send("380670000001", "Ваше замовлення прийнято"))

    assert (result.sent, result.response) == (True, ACCEPTED)
    request = api.requests[0]
    assert (str(request.url), request.headers["Authorization"]) == (
        "https://im.smsclub.mobi/sms/send",
        "Bearer test-token",
    )
    body = json.loads(request.content)
    assert (body["phone"], body["message"], body["src_addr"]) == (
        ["380670000001"],
        "Ваше замовлення прийнято",
        "Vidro.ua",
    )


@pytest.mark.parametrize(
    "reply",
    [
        '{"success_request":{"info":[],"add_info":{"380670000001":"Неправильний номер"}}}',
        '{"error":"Unauthorized"}',
        "not json",
    ],
)
def test_rejected_or_unknown_reply_is_not_sent_but_keeps_the_response(reply):
    sender = SmsClubSender(SmsClubSettings(token="t"), httpx.MockTransport(FakeSmsClub(reply)))

    result = asyncio.run(sender.send("380670000001", "Текст"))

    assert (result.sent, result.response) == (False, reply)


def test_phone_outside_only_phones_is_not_requested():
    api = FakeSmsClub(ACCEPTED)
    sender = SmsClubSender(SmsClubSettings(token="t", only_phones=["380990000000"]), httpx.MockTransport(api))

    result = asyncio.run(sender.send("380670000001", "Текст"))

    assert (result.sent, result.response) == (False, NOT_ALLOWED_RESPONSE)
    assert api.requests == []
