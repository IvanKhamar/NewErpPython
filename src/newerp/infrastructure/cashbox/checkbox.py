"""The Checkbox RRO API client and its stub."""

import asyncio
import json
import uuid
from typing import Any
from urllib.parse import quote

import httpx

from newerp.application.cashbox.models import CheckboxShiftState
from newerp.config import CheckboxCashier, CheckboxSettings
from newerp.domain.cashbox import FiscalReceipt, ReceiptPaymentType
from newerp.domain.common import DomainError

CLOSE_POLL_ATTEMPTS = 5
TIMEOUT_SECONDS = 30


class HttpCheckboxClient:
    """
    Each cashier signs in with login and password; the token is kept per cashier and taken again once on 401.
    Closing a shift in Checkbox is asynchronous, so the Z report is polled a few times.
    """

    def __init__(
        self, settings: CheckboxSettings, transport: httpx.AsyncBaseTransport | None = None, close_poll_delay: float = 1
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._close_poll_delay = close_poll_delay
        self._tokens: dict[int, str] = {}

    is_fake = False

    def can_use(self, cashier_id: int) -> bool:
        return self._find(cashier_id) is not None

    async def open_shift(self, cashier_id: int) -> str:
        current = await self._send(cashier_id, "GET", "/api/v1/cashier/shift")
        if isinstance(current, dict) and _str(current.get("id")) and _str(current.get("status")) != "CLOSED":
            return _str(current["id"])
        created = await self._send(cashier_id, "POST", "/api/v1/shifts", with_license=True)
        shift_id = _str((created or {}).get("id"))
        if not shift_id:
            raise DomainError("Checkbox не повернув id нової зміни.")
        return shift_id

    async def get_shift(self, cashier_id: int, shift_id: str) -> CheckboxShiftState:
        shift = await self._send(cashier_id, "GET", f"/api/v1/shifts/{quote(shift_id, safe='')}") or {}
        return CheckboxShiftState(
            shift_id=shift_id,
            is_closed=_str(shift.get("status")) == "CLOSED",
            z_report_id=_str((shift.get("z_report") or {}).get("id")) or None,
        )

    async def close_shift(self, cashier_id: int) -> str | None:
        shift = await self._send(cashier_id, "POST", "/api/v1/shifts/close") or {}
        z_report_id = _str((shift.get("z_report") or {}).get("id"))
        if z_report_id:
            return z_report_id
        shift_id = _str(shift.get("id"))
        if not shift_id:
            return None
        for _ in range(CLOSE_POLL_ATTEMPTS):
            await asyncio.sleep(self._close_poll_delay)
            state = await self.get_shift(cashier_id, shift_id)
            if state.z_report_id:
                return state.z_report_id
        return None

    async def create_x_report(self, cashier_id: int) -> str:
        report = await self._send(cashier_id, "POST", "/api/v1/reports") or {}
        report_id = _str(report.get("id"))
        if not report_id:
            raise DomainError("Checkbox не повернув id X-звіту.")
        return report_id

    async def get_report_text(self, cashier_id: int, report_id: str) -> str:
        response = await self._send_raw(cashier_id, "GET", f"/api/v1/reports/{quote(report_id, safe='')}/text")
        return response.text

    async def sell(self, cashier_id: int, receipt: FiscalReceipt) -> str:
        body: dict[str, Any] = {
            "cashier_name": receipt.cashier_name,
            "goods": [
                {"good": {"code": g.code, "name": g.name, "price": g.price_kopecks}, "quantity": g.quantity_thousandths}
                for g in receipt.goods
            ],
            "payments": [
                {
                    "type": "CASH" if receipt.payment.type == ReceiptPaymentType.CASH else "CASHLESS",
                    "value": receipt.payment.value_kopecks,
                    "label": receipt.payment.label,
                }
            ],
            "footer": receipt.footer,
        }
        if receipt.discount_kopecks > 0:
            body["discounts"] = [{"type": "DISCOUNT", "mode": "VALUE", "value": receipt.discount_kopecks}]
        if receipt.email is not None:
            body["delivery"] = {"email": receipt.email}
        created = await self._send(cashier_id, "POST", "/api/v1/receipts/sell", body) or {}
        receipt_id = _str(created.get("id"))
        if not receipt_id:
            raise DomainError("Checkbox не повернув id чека.")
        return receipt_id

    def receipt_url(self, receipt_id: str) -> str | None:
        return f"{self._settings.base_url.rstrip('/')}/api/v1/receipts/{quote(receipt_id, safe='')}/html"

    def _find(self, cashier_id: int) -> CheckboxCashier | None:
        return next(
            (
                c
                for c in self._settings.cashiers
                if c.cashier_id == cashier_id and c.login and c.password.get_secret_value()
            ),
            None,
        )

    async def _send(self, cashier_id: int, method: str, path: str, body: dict | None = None, with_license=False):
        response = await self._send_raw(cashier_id, method, path, body, with_license)
        return json.loads(response.text) if response.text.strip() else None

    async def _send_raw(self, cashier_id: int, method: str, path: str, body=None, with_license=False) -> httpx.Response:
        cashier = self._find(cashier_id)
        if cashier is None:
            raise DomainError(f"Для касира {cashier_id} не задано ключі Checkbox.")
        async with self._http() as http:
            for attempt in range(2):
                token = self._tokens.get(cashier_id) or await self._sign_in(http, cashier)
                headers = {"Authorization": f"Bearer {token}"}
                if with_license:
                    headers["X-License-Key"] = cashier.license_key.get_secret_value()
                response = await http.request(method, path, json=body, headers=headers)
                if response.status_code == 401 and attempt == 0:
                    self._tokens.pop(cashier_id, None)  # the token expired: sign in again once
                    continue
                _ensure_success(response)
                return response
        raise DomainError("Checkbox не прийняв токен касира.")  # not reached: the second 401 raises above

    async def _sign_in(self, http: httpx.AsyncClient, cashier: CheckboxCashier) -> str:
        response = await http.post(
            "/api/v1/cashier/signin", json={"login": cashier.login, "password": cashier.password.get_secret_value()}
        )
        _ensure_success(response)
        token = _str(json.loads(response.text).get("access_token"))
        if not token:
            raise DomainError("Checkbox не повернув токен касира.")
        self._tokens[cashier.cashier_id] = token
        return token

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._settings.base_url.rstrip("/"),
            timeout=TIMEOUT_SECONDS,
            transport=self._transport,
            headers={"X-Client-Name": self._settings.client_name, "X-Client-Version": self._settings.client_version},
        )


def _ensure_success(response: httpx.Response) -> None:
    if response.is_success:
        return
    message = None
    try:
        message = _str(json.loads(response.text).get("message")) or None
    except (ValueError, AttributeError):
        pass
    raise DomainError(f"Checkbox відмовив ({response.status_code}): {message or response.text}")


def _str(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value)


class StubCheckboxClient:
    """Without keys: with fake_receipts it "fiscalizes" test receipts in memory, otherwise it can do nothing."""

    def __init__(self, fake_receipts: bool = False) -> None:
        self._fake = fake_receipts
        self._open_shifts: dict[int, str] = {}

    is_fake = True

    def can_use(self, cashier_id: int) -> bool:
        return self._fake

    async def open_shift(self, cashier_id: int) -> str:
        return self._open_shifts.setdefault(cashier_id, f"test-shift-{uuid.uuid4().hex}")

    async def get_shift(self, cashier_id: int, shift_id: str) -> CheckboxShiftState:
        return CheckboxShiftState(shift_id=shift_id, is_closed=False, z_report_id=None)

    async def close_shift(self, cashier_id: int) -> str | None:
        self._open_shifts.pop(cashier_id, None)
        return f"test-z-{uuid.uuid4().hex}"

    async def create_x_report(self, cashier_id: int) -> str:
        return f"test-x-{uuid.uuid4().hex}"

    async def get_report_text(self, cashier_id: int, report_id: str) -> str:
        return f"ТЕСТОВИЙ ЗВІТ (заглушка Checkbox, не фіскальний)\nЗвіт: {report_id}"

    async def sell(self, cashier_id: int, receipt: FiscalReceipt) -> str:
        return f"test-{uuid.uuid4().hex}"

    def receipt_url(self, receipt_id: str) -> str | None:
        return None


def checkbox_client(settings: CheckboxSettings) -> HttpCheckboxClient | StubCheckboxClient:
    """The real client only with some cashier's keys; otherwise the stub."""
    if any(c.login and c.password.get_secret_value() for c in settings.cashiers):
        return HttpCheckboxClient(settings)
    return StubCheckboxClient(settings.fake_receipts)
