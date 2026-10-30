"""Bank payments (PrivatBank statement, matched to orders by hand with suggestions) and LiqPay payment links."""

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol

from newerp.application.common import Clock, Dto, Money, NotFoundError, PagedResult
from newerp.application.orders.ports import OrderQueries
from newerp.domain.common import DomainError
from newerp.domain.payments import (
    LIQPAY_LABELS,
    BankPayment,
    LiqPayState,
    MatchReason,
    OrderCandidate,
    interpret_liqpay,
    liqpay_description,
    order_numbers_in,
    rank,
    validate_liqpay_amount,
)

SUGGESTION_LIMIT = 10
SUGGESTION_DAYS = 60  # orders up to this many days before the payment
MAX_IMPORT_DAYS = 31


class BankPaymentDto(Dto):
    id: int
    app_code: str
    card: str
    amount: Money
    balance: Money
    payer: str
    description: str
    timestamp: datetime
    order_id: int
    order_customer: str | None
    is_visible: bool


@dataclass(frozen=True)
class BankPaymentFilter:
    from_: date | None = None
    to: date | None = None
    linked: bool | None = None
    order_id: int | None = None
    search: str | None = None
    hidden: bool = False
    page: int = 1
    page_size: int = 50


@dataclass(frozen=True)
class BankTransaction:
    """A statement row as the bank gives it."""

    id: str
    account: str
    date: date
    time: time
    amount: Decimal
    balance: Decimal | None
    is_incoming: bool
    purpose: str
    payer: str
    payer_account: str
    payer_bank_code: str
    payer_tax_code: str


class BankImportResult(Dto):
    """client_configured False: no bank keys, nothing was asked."""

    client_configured: bool
    fetched: int
    added: int
    skipped: int


class PaymentSuggestionDto(Dto):
    order_id: int
    customer: str
    telephone: str
    total: Money
    prepayment: Money
    date_added: datetime
    status: str
    linked_payment_id: int | None
    reasons: MatchReason
    score: int


class LinkPaymentRequest(Dto):
    """order_id 0: unlink."""

    order_id: int = 0


@dataclass(frozen=True)
class CandidateQuery:
    amounts: list[Decimal]
    order_ids: list[int]
    customer: str | None
    from_: datetime
    to: datetime
    limit: int


class BankPaymentRepository(Protocol):
    async def list_payments(self, payment_filter: BankPaymentFilter) -> PagedResult[BankPaymentDto]: ...

    async def get(self, payment_id: int) -> BankPayment | None: ...

    async def get_dto(self, payment_id: int) -> BankPaymentDto | None: ...

    async def list_for_order(self, order_id: int) -> list[BankPaymentDto]: ...

    async def existing_app_codes(self, app_codes: list[str]) -> set[str]: ...

    async def find_candidates(self, query: CandidateQuery) -> list[OrderCandidate]: ...

    async def order_exists(self, order_id: int) -> bool: ...

    def add(self, payment: BankPayment) -> None: ...

    async def save_changes(self) -> None: ...


class BankStatementClient(Protocol):
    @property
    def is_configured(self) -> bool: ...

    async def fetch(self, from_: date, to: date) -> list[BankTransaction]: ...


class BankPaymentService:
    def __init__(self, repository: BankPaymentRepository, client: BankStatementClient, clock: Clock) -> None:
        self._repository = repository
        self._client = client
        self._clock = clock

    async def list_payments(self, payment_filter: BankPaymentFilter) -> PagedResult[BankPaymentDto]:
        return await self._repository.list_payments(payment_filter)

    async def for_order(self, order_id: int) -> list[BankPaymentDto]:
        return await self._repository.list_for_order(order_id)

    async def import_statement(self, from_: date | None, to: date | None) -> BankImportResult:
        """
        Incoming payments of the period (by default yesterday and today), without LiqPay payouts (they are matched
        through LiqPay). A row already loaded (the same bank id) is skipped, so importing again is safe.
        """
        if not self._client.is_configured:
            return BankImportResult(client_configured=False, fetched=0, added=0, skipped=0)
        end = to or self._clock.now().date()
        start = from_ or end - timedelta(days=1)
        if start > end:
            raise DomainError("Початок періоду пізніше за кінець.")
        if (end - start).days > MAX_IMPORT_DAYS:
            raise DomainError(f"Завантажувати можна не більше ніж {MAX_IMPORT_DAYS} днів за раз.")

        transactions = await self._client.fetch(start, end)
        incoming: dict[str, BankTransaction] = {}
        for t in transactions:
            if t.is_incoming and t.id and "liqpay" not in t.purpose.casefold():
                incoming.setdefault(t.id, t)
        existing = await self._repository.existing_app_codes(list(incoming))
        added = [t for t in incoming.values() if t.id not in existing]
        for t in added:
            self._repository.add(
                BankPayment(t.id, t.account, t.amount, t.balance, _describe(t), t.payer, t.date, t.time)
            )
        await self._repository.save_changes()
        return BankImportResult(
            client_configured=True, fetched=len(transactions), added=len(added), skipped=len(transactions) - len(added)
        )

    async def suggest(self, payment_id: int, customer: str | None) -> list[PaymentSuggestionDto]:
        """Orders the payment probably belongs to (by sum, number and phone in the purpose), the best first."""
        payment = await self._require(payment_id)
        amounts = list(dict.fromkeys([payment.amount, Decimal(math.floor(payment.amount))]))
        query = CandidateQuery(
            amounts=amounts,
            order_ids=order_numbers_in(f"{payment.description} {payment.terminal}"),
            customer=customer.strip() if customer and customer.strip() else None,
            from_=payment.timestamp - timedelta(days=SUGGESTION_DAYS),
            to=payment.timestamp + timedelta(days=1),
            limit=SUGGESTION_LIMIT * 5,
        )
        candidates = await self._repository.find_candidates(query)
        return [
            PaymentSuggestionDto(
                order_id=s.order.order_id,
                customer=s.order.customer,
                telephone=s.order.telephone,
                total=s.order.total,
                prepayment=s.order.prepayment,
                date_added=s.order.date_added,
                status=s.order.status,
                linked_payment_id=s.order.linked_payment_id,
                reasons=s.reasons,
                score=s.score,
            )  # fmt: skip
            for s in rank(payment, candidates)[:SUGGESTION_LIMIT]
        ]

    async def link(self, payment_id: int, request: LinkPaymentRequest) -> BankPaymentDto:
        payment = await self._require(payment_id)
        if request.order_id == 0:
            payment.unlink()
        elif await self._repository.order_exists(request.order_id):
            payment.link_to(request.order_id)
        else:
            raise NotFoundError(f"Замовлення {request.order_id} не знайдено.")
        await self._repository.save_changes()
        return await self._repository.get_dto(payment_id)

    async def set_visibility(self, payment_id: int, visible: bool) -> None:
        """A payment that is not about orders can be hidden from the list (and shown again)."""
        payment = await self._require(payment_id)
        if visible:
            payment.show()
        else:
            payment.hide()
        await self._repository.save_changes()

    async def _require(self, payment_id: int) -> BankPayment:
        payment = await self._repository.get(payment_id)
        if payment is None:
            raise NotFoundError(f"Платіж {payment_id} не знайдено.")
        return payment


def _describe(t: BankTransaction) -> str:
    lines = [t.purpose]
    if t.payer_account:
        lines.append(f"Рахунок: {t.payer_account}")
    if t.payer_bank_code:
        lines.append(f"МФО: {t.payer_bank_code}")
    if t.payer_tax_code:
        lines.append(f"ЄДРПОУ: {t.payer_tax_code}")
    return "\n".join(line for line in lines if line)


# ── LiqPay ──


class CreatePaymentLinkRequest(Dto):
    """amount None: the whole order."""

    amount: Decimal | None = None


class PaymentLinkDto(Dto):
    url: str
    amount: Money
    currency: str
    is_test: bool


class LiqPayStatusDto(Dto):
    state: LiqPayState
    label: str
    amount: Money | None
    paid_at: datetime | None
    is_test: bool


@dataclass(frozen=True)
class LiqPayCheckout:
    order_id: str
    amount: Decimal
    currency: str
    description: str


@dataclass(frozen=True)
class LiqPayStatusReply:
    status: str | None
    error_code: str | None
    amount: Decimal | None
    end_date: datetime | None


class LiqPayGateway(Protocol):
    @property
    def is_test(self) -> bool:
        """No keys: links are signed with sandbox test keys."""
        ...

    def checkout_url(self, checkout: LiqPayCheckout) -> str: ...

    async def status(self, order_id: str) -> LiqPayStatusReply: ...


class LiqPayService:
    def __init__(self, orders: OrderQueries, gateway: LiqPayGateway) -> None:
        self._orders = orders
        self._gateway = gateway

    async def create_link(self, order_id: int, request: CreatePaymentLinkRequest) -> PaymentLinkDto:
        order = await self._orders.get(order_id)
        if order is None:
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")
        amount = validate_liqpay_amount(request.amount, order.total)
        currency = order.currency_code.strip() or "UAH"
        checkout = LiqPayCheckout(
            str(order_id), amount, currency, liqpay_description(order_id, order.customer, order.telephone)
        )
        return PaymentLinkDto(
            url=self._gateway.checkout_url(checkout), amount=amount, currency=currency, is_test=self._gateway.is_test
        )

    async def status(self, order_id: int) -> LiqPayStatusDto:
        if await self._orders.get(order_id) is None:
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")
        reply = await self._gateway.status(str(order_id))
        state = interpret_liqpay(reply.status, reply.error_code)
        return LiqPayStatusDto(
            state=state, label=LIQPAY_LABELS[state], amount=reply.amount,
            paid_at=reply.end_date if state == LiqPayState.PAID else None, is_test=self._gateway.is_test,
        )  # fmt: skip
