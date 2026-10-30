"""Payments: PrivatBank statement rows matched to orders, and LiqPay card payments."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import IntEnum, IntFlag

from newerp.domain.common import DomainError


class BankPayment:
    """
    An incoming bank payment (dd_privatbank of the old site). The old site stores sums as text ("1500.00 UAH"),
    so they are kept that way and read back with parse_money.
    """

    def __init__(
        self, app_code: str, card: str, amount: Decimal, balance: Decimal | None, description: str, terminal: str,
        tran_date: date, tran_time: time,
    ) -> None:  # fmt: skip
        self.id: int | None = None
        self.order_id = 0
        self.app_code = app_code  # the bank's transaction id
        self.card = card  # the account the money came to
        self.amount_text = money_text(amount)
        self.card_amount_text = self.amount_text
        self.rest_text = money_text(balance or Decimal(0))
        self.description = description
        self.terminal = terminal[:255]  # the payer's name
        self.tran_date = tran_date
        self.tran_time = tran_time
        self.is_visible = True

    @property
    def amount(self) -> Decimal:
        return parse_money(self.amount_text)

    @property
    def timestamp(self) -> datetime:
        return datetime.combine(self.tran_date, self.tran_time)

    def link_to(self, order_id: int) -> None:
        self.order_id = order_id

    def unlink(self) -> None:
        self.order_id = 0

    def hide(self) -> None:
        self.is_visible = False

    def show(self) -> None:
        self.is_visible = True


def money_text(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), ROUND_HALF_EVEN)} UAH"


def parse_money(text: str | None) -> Decimal:
    number = re.sub(r"[^\d,.\-]", "", text or "").replace(",", ".")
    try:
        return Decimal(number) if number else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


@dataclass(frozen=True)
class OrderCandidate:
    """An order a payment may belong to. linked_payment_id: a payment already linked to it."""

    order_id: int
    customer: str
    telephone: str
    total: Decimal
    prepayment: Decimal
    date_added: datetime
    status: str
    linked_payment_id: int | None


class MatchReason(IntFlag):
    """Why an order fits a payment; flags combine (a number in JSON, like a [Flags] enum in .NET)."""

    NONE = 0
    ORDER_NUMBER = 1
    TOTAL = 2
    PREPAYMENT = 4
    PHONE = 8
    NAME = 16


@dataclass(frozen=True)
class ScoredCandidate:
    order: OrderCandidate
    reasons: MatchReason
    score: int


def order_numbers_in(text: str | None) -> list[int]:
    """Numbers of 3–7 digits that are not part of a sum ("2045", not "1500.00")."""
    return list(dict.fromkeys(int(m) for m in re.findall(r"(?<![\d.,])\d{3,7}(?!\d|[.,]\d)", text or "")))


def rank(payment: BankPayment, candidates: Iterable[OrderCandidate]) -> list[ScoredCandidate]:
    """
    Scores orders by what the payment says: the order number in the purpose, the sum (total, advance or the rest),
    the phone and the surname. An order that already has another payment loses points.
    """
    text = f"{payment.description} {payment.terminal}"
    digits = re.sub(r"\D", "", text)
    numbers = set(order_numbers_in(text))
    lower = text.lower()
    amount = payment.amount

    scored = []
    for c in candidates:
        reasons = MatchReason.NONE
        if c.order_id in numbers:
            reasons |= MatchReason.ORDER_NUMBER
        if c.total == amount:
            reasons |= MatchReason.TOTAL
        elif c.prepayment > 0 and (c.prepayment == amount or c.total - c.prepayment == amount):
            reasons |= MatchReason.PREPAYMENT
        phone = re.sub(r"\D", "", c.telephone)
        if len(phone) >= 9 and phone[-9:] in digits:
            reasons |= MatchReason.PHONE
        words = c.customer.split()
        if words and len(words[-1]) >= 3 and words[-1].lower() in lower:
            reasons |= MatchReason.NAME
        score = (
            (50 if MatchReason.ORDER_NUMBER in reasons else 0)
            + (30 if MatchReason.PHONE in reasons else 0)
            + (25 if MatchReason.TOTAL in reasons else 0)
            + (20 if MatchReason.PREPAYMENT in reasons else 0)
            + (15 if MatchReason.NAME in reasons else 0)
            - (40 if c.linked_payment_id is not None and c.linked_payment_id != payment.id else 0)
        )
        scored.append(ScoredCandidate(c, reasons, score))
    return sorted(scored, key=lambda s: (s.score, s.order.date_added), reverse=True)


class LiqPayState(IntEnum):
    NOT_FOUND = 0
    WAITING = 1
    PAID = 2
    FAILED = 3
    REVERSED = 4


LIQPAY_LABELS = {
    LiqPayState.NOT_FOUND: "Ще не оплачено",
    LiqPayState.WAITING: "Оплата в процесі",
    LiqPayState.PAID: "Оплачено",
    LiqPayState.FAILED: "Оплата не вдалася",
    LiqPayState.REVERSED: "Гроші повернено",
}


def validate_liqpay_amount(amount: Decimal | None, order_total: Decimal) -> Decimal:
    """Not given: the whole order. Never more than the order (a part is fine: an advance)."""
    value = (amount if amount is not None else order_total).quantize(Decimal("0.01"), ROUND_HALF_EVEN)
    if value <= 0:
        raise DomainError("Сума оплати має бути більшою за нуль.")
    if value > order_total:
        raise DomainError(f"Сума оплати не може перевищувати суму замовлення ({order_total:.2f}).")
    return value


def liqpay_description(order_id: int, customer: str, telephone: str) -> str:
    return f"Оплата замовлення #{order_id}.\nПокупець: {customer.strip()}. \nТелефон: {telephone.strip()}"


def interpret_liqpay(status: str | None, error_code: str | None) -> LiqPayState:
    """LiqPay's status answer as a state; wait_accept and wait_compensation already mean the money is taken."""
    if error_code == "payment_not_found":
        return LiqPayState.NOT_FOUND
    if status in ("success", "sandbox", "wait_accept", "wait_compensation"):
        return LiqPayState.PAID
    if status == "reversed":
        return LiqPayState.REVERSED
    if status in ("failure", "error"):
        return LiqPayState.FAILED
    if not status and error_code is not None:
        raise DomainError(f"LiqPay відповів помилкою: {error_code}.")
    return LiqPayState.WAITING
