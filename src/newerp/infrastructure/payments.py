"""Bank payments (dd_privatbank), the PrivatBank statement client and the LiqPay gateway."""

import base64
import hashlib
import json
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import Boolean, Column, Date, Integer, String, Table, Text, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.common import PagedResult, normalize_paging
from newerp.application.payments import (
    BankPaymentDto,
    BankPaymentFilter,
    BankTransaction,
    CandidateQuery,
    LiqPayCheckout,
    LiqPayStatusReply,
)
from newerp.config import LiqPaySettings, PrivatBankSettings
from newerp.domain.common import DomainError
from newerp.domain.payments import BankPayment, OrderCandidate, parse_money
from newerp.infrastructure.db import Base
from newerp.infrastructure.models.orders import DdOrder, DdOrderStatus
from newerp.infrastructure.orders.queries import parse_int
from newerp.infrastructure.types import TimeOfDay

TIMEOUT_SECONDS = 60

bank_payments = Table(
    "dd_privatbank",
    Base.metadata,
    Column("payment_id", Integer, primary_key=True),
    Column("order_id", Integer),
    Column("appcode", String(64)),
    Column("card", String(64)),
    Column("amount", String(32)),
    Column("cardamount", String(32)),
    Column("rest", String(32)),
    Column("description", Text),
    Column("terminal", String(255)),
    Column("trandate", Date),
    Column("trantime", TimeOfDay),
    Column("show_status", Boolean),
)

Base.registry.map_imperatively(
    BankPayment,
    bank_payments,
    properties={
        "id": bank_payments.c.payment_id,
        "app_code": bank_payments.c.appcode,
        "amount_text": bank_payments.c.amount,
        "card_amount_text": bank_payments.c.cardamount,
        "rest_text": bank_payments.c.rest,
        "tran_date": bank_payments.c.trandate,
        "tran_time": bank_payments.c.trantime,
        "is_visible": bank_payments.c.show_status,
    },
)


class SqlBankPaymentRepository:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def list_payments(self, payment_filter: BankPaymentFilter) -> PagedResult[BankPaymentDto]:
        f, p = payment_filter, bank_payments.c
        page, page_size = normalize_paging(f.page, f.page_size)
        conditions = [p.show_status.is_(not f.hidden)]
        if f.from_ is not None:
            conditions.append(p.trandate >= f.from_)
        if f.to is not None:
            conditions.append(p.trandate <= f.to)
        if f.linked is not None:
            conditions.append(p.order_id != 0 if f.linked else p.order_id == 0)
        if f.order_id is not None:
            conditions.append(p.order_id == f.order_id)
        if f.search and f.search.strip():
            search = f.search.strip()
            conditions.append(
                or_(
                    p.description.contains(search, autoescape=True),
                    p.terminal.contains(search, autoescape=True),
                    p.amount.startswith(search, autoescape=True),
                )
            )
        total = await self._session.scalar(select(func.count()).select_from(bank_payments).where(*conditions))
        items = await self._project(
            conditions, [p.trandate.desc(), p.trantime.desc(), p.payment_id.desc()], (page - 1) * page_size, page_size
        )
        return PagedResult[BankPaymentDto](items=items, page=page, page_size=page_size, total_count=total or 0)

    async def get(self, payment_id: int) -> BankPayment | None:
        return await self._session.get(BankPayment, payment_id)

    async def get_dto(self, payment_id: int) -> BankPaymentDto | None:
        rows = await self._project([bank_payments.c.payment_id == payment_id], [])
        return rows[0] if rows else None

    async def list_for_order(self, order_id: int) -> list[BankPaymentDto]:
        p = bank_payments.c
        return await self._project([p.order_id == order_id], [p.trandate, p.trantime])

    async def existing_app_codes(self, app_codes: list[str]) -> set[str]:
        if not app_codes:
            return set()
        return set(
            await self._session.scalars(select(bank_payments.c.appcode).where(bank_payments.c.appcode.in_(app_codes)))
        )

    async def find_candidates(self, query: CandidateQuery) -> list[OrderCandidate]:
        matches = [DdOrder.order_id.in_(query.order_ids)] if query.order_ids else []
        if query.customer is not None:
            customer = query.customer
            matches.append(
                or_(
                    DdOrder.firstname.contains(customer, autoescape=True),
                    DdOrder.lastname.contains(customer, autoescape=True),
                    DdOrder.telephone.contains(customer, autoescape=True),
                    DdOrder.order_id == (parse_int(customer) or -1),
                )
            )
        matches.append(
            (DdOrder.date_added >= query.from_)
            & (DdOrder.date_added < query.to)
            & or_(
                DdOrder.total.in_(query.amounts),
                DdOrder.prepayment.in_(query.amounts),
                (DdOrder.total - DdOrder.prepayment).in_(query.amounts),
            )
        )
        linked = (
            select(func.min(bank_payments.c.payment_id))
            .where(bank_payments.c.order_id == DdOrder.order_id)
            .scalar_subquery()
        )
        status = select(DdOrderStatus).where(DdOrderStatus.language_id == self._language_id).subquery()
        rows = await self._session.execute(
            select(
                DdOrder.order_id,
                DdOrder.firstname,
                DdOrder.lastname,
                DdOrder.telephone,
                DdOrder.total,
                DdOrder.prepayment,
                DdOrder.date_added,
                func.coalesce(status.c.name, ""),
                linked,
            )  # fmt: skip
            .outerjoin(status, status.c.order_status_id == DdOrder.order_status_id)
            .where(DdOrder.order_status_id != 0, or_(*matches))
            .order_by(DdOrder.order_id.desc())
            .limit(query.limit)
        )
        return [
            OrderCandidate(
                order_id=r[0],
                customer=f"{r[1]} {r[2]}".strip(),
                telephone=r[3],
                total=r[4],
                prepayment=r[5],
                date_added=r[6],
                status=r[7],
                linked_payment_id=r[8],
            )  # fmt: skip
            for r in rows
        ]

    async def order_exists(self, order_id: int) -> bool:
        return bool(await self._session.scalar(select(exists().where(DdOrder.order_id == order_id))))

    def add(self, payment: BankPayment) -> None:
        self._session.add(payment)

    async def save_changes(self) -> None:
        await self._session.commit()

    async def _project(self, conditions, order_by, offset: int = 0, limit: int | None = None) -> list[BankPaymentDto]:
        query = (
            select(BankPayment, DdOrder.firstname, DdOrder.lastname)
            .outerjoin(DdOrder, DdOrder.order_id == bank_payments.c.order_id)
            .where(*conditions)
            .order_by(*order_by)
            .offset(offset)
        )
        if limit is not None:
            query = query.limit(limit)
        return [
            BankPaymentDto(
                id=p.id,
                app_code=p.app_code,
                card=p.card,
                amount=p.amount,
                balance=parse_money(p.rest_text),
                payer=p.terminal,
                description=p.description,
                timestamp=p.timestamp,
                order_id=p.order_id,
                order_customer=f"{first} {last}".strip() if first is not None else None,
                is_visible=p.is_visible,
            )  # fmt: skip
            for p, first, last in await self._session.execute(query)
        ]


# ── PrivatBank "Автоклієнт" statement ──

DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M")
TIME_FORMATS = ("%H:%M", "%H:%M:%S")


class HttpPrivatBankClient:
    def __init__(self, settings: PrivatBankSettings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    @property
    def is_configured(self) -> bool:
        return self._settings.is_configured

    async def fetch(self, from_: date, to: date) -> list[BankTransaction]:
        result: list[BankTransaction] = []
        url = f"{self._settings.base_url.rstrip('/')}/api/proxy/transactions"
        params = {"startDate": from_.strftime("%d-%m-%Y"), "endDate": to.strftime("%d-%m-%Y")}
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=self._transport) as http:
            for account in self._settings.accounts:
                token = account.token.get_secret_value()
                if not account.id or not token:
                    continue
                response = await http.get(url, params=params, headers={"id": account.id, "token": token})
                text = _decode(response.content)
                if not response.is_success:
                    raise DomainError(f"ПриватБанк відмовив ({response.status_code}): {_trim(text)}")
                result += parse_statement(text)
        return result


def parse_statement(text: str) -> list[BankTransaction]:
    """{"StatementsResponse": {"statements": [{"<account>": [{"0": {...}}, ...]}]}}"""
    try:
        statements = json.loads(text)["StatementsResponse"]["statements"]
    except (ValueError, KeyError, TypeError) as error:
        raise DomainError(f"ПриватБанк повернув невідому відповідь: {_trim(text)}") from error
    if not isinstance(statements, list):
        raise DomainError(f"ПриватБанк повернув невідому відповідь: {_trim(text)}")

    result = []
    for statement in statements:
        for account, payments in statement.items() if isinstance(statement, dict) else []:
            for wrapper in payments if isinstance(payments, list) else []:
                p = next(iter(wrapper.values()), None) if isinstance(wrapper, dict) else None
                if not isinstance(p, dict):
                    continue
                result.append(
                    BankTransaction(
                        id=_str(p, "ID"),
                        account=account,
                        date=_date(_str(p, "BPL_DAT_KL")),
                        time=_time(_str(p, "BPL_TIM_P")),
                        amount=_money(_str(p, "BPL_SUM_E")) or Decimal(0),
                        balance=_money(_str(p, "BPL_C_BAL")),
                        is_incoming=_str(p, "TRANTYPE") != "D",
                        purpose=_str(p, "BPL_OSND"),
                        payer=_str(p, "BPL_A_NAM"),
                        payer_account=_str(p, "BPL_A_ACC"),
                        payer_bank_code=_str(p, "BPL_A_MFO"),
                        payer_tax_code=_str(p, "BPL_A_CRF"),
                    )  # fmt: skip
                )
    return result


def _decode(content: bytes) -> str:
    """The bank answers in UTF-8, but older accounts in Windows-1251."""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1251")


def _date(text: str) -> date:
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    raise DomainError(f"ПриватБанк повернув незрозумілу дату: «{text}».")


def _time(text: str) -> time:
    for time_format in TIME_FORMATS:
        try:
            return datetime.strptime(text, time_format).time()
        except ValueError:
            continue
    return time.min


def _money(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ".").replace(" ", "")) if text else None
    except InvalidOperation:
        return None


def _str(item: dict, name: str) -> str:
    value = item.get(name)
    return "" if value is None or isinstance(value, (dict, list)) else str(value).strip()


def _trim(text: str) -> str:
    return text[:300] + "…" if len(text) > 300 else text


class StubBankStatementClient:
    is_configured = False

    async def fetch(self, from_: date, to: date) -> list[BankTransaction]:
        return []


def bank_statement_client(settings: PrivatBankSettings) -> HttpPrivatBankClient | StubBankStatementClient:
    return HttpPrivatBankClient(settings) if settings.is_configured else StubBankStatementClient()


# ── LiqPay ──


class HttpLiqPayGateway:
    """Checkout links and the status request, both signed: base64(SHA1(private_key + data + private_key))."""

    def __init__(self, settings: LiqPaySettings, transport: httpx.AsyncBaseTransport | None = None, test=False):
        self._settings = settings
        self._transport = transport
        self._test = test

    @property
    def is_test(self) -> bool:
        return self._test

    def checkout_url(self, checkout: LiqPayCheckout) -> str:
        data, signature = self.sign(
            {
                "public_key": self._settings.public_key,
                "version": "3",
                "action": "pay",
                "amount": float(checkout.amount),
                "currency": checkout.currency,
                "description": checkout.description,
                "order_id": checkout.order_id,
                "language": "uk",
            }  # fmt: skip
        )
        query = httpx.QueryParams({"data": data, "signature": signature})
        return f"{self._settings.base_url.rstrip('/')}/api/3/checkout?{query}"

    async def status(self, order_id: str) -> LiqPayStatusReply:
        if self._test:
            return LiqPayStatusReply(status=None, error_code="payment_not_found", amount=None, end_date=None)
        data, signature = self.sign(
            {"action": "status", "version": "3", "public_key": self._settings.public_key, "order_id": order_id}
        )
        async with httpx.AsyncClient(timeout=30, transport=self._transport) as http:
            response = await http.post(
                f"{self._settings.base_url.rstrip('/')}/api/request", data={"data": data, "signature": signature}
            )
        if not response.is_success:
            raise DomainError(f"LiqPay недоступний: {response.status_code}.")
        return parse_liqpay_status(response.text)

    def sign(self, parameters: dict[str, Any]) -> tuple[str, str]:
        data = base64.b64encode(json.dumps(parameters, separators=(",", ":")).encode()).decode()
        key = self._settings.private_key.get_secret_value()
        signature = base64.b64encode(hashlib.sha1((key + data + key).encode()).digest()).decode()
        return data, signature


def parse_liqpay_status(text: str) -> LiqPayStatusReply:
    try:
        root = json.loads(text)
    except ValueError as error:
        raise DomainError("LiqPay повернув незрозумілу відповідь.") from error

    def number(value: Any) -> Decimal | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return Decimal(str(value))
        if isinstance(value, str):
            try:
                return Decimal(value)
            except InvalidOperation:
                return None
        return None

    end_ms = number(root.get("end_date"))
    return LiqPayStatusReply(
        status=root.get("status") if isinstance(root.get("status"), str) else None,
        error_code=root.get("err_code") if isinstance(root.get("err_code"), str) else None,
        amount=number(root.get("amount")),
        end_date=datetime.fromtimestamp(int(end_ms) / 1000, UTC) if end_ms is not None else None,
    )


def liqpay_gateway(settings: LiqPaySettings) -> HttpLiqPayGateway:
    """Without keys the links are signed with sandbox test keys and the status is never "paid"."""
    if settings.is_configured:
        return HttpLiqPayGateway(settings)
    test = LiqPaySettings(
        base_url=settings.base_url, public_key="sandbox_newerp_test", private_key="sandbox_newerp_test"
    )
    return HttpLiqPayGateway(test, test=True)
