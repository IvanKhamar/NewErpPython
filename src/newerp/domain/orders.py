"""Order rules: status changes, manual order drafts, customer risk."""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import IntEnum

from newerp.domain.common import DomainError


@dataclass(frozen=True)
class OrderStatusChange:
    """A status change to write to the log."""

    order_id: int
    old_status_id: int
    new_status_id: int
    user_id: int
    changed_at: datetime

    @staticmethod
    def create(order_id: int, current_status_id: int, new_status_id: int, user_id: int, now: datetime):
        if new_status_id <= 0:
            raise DomainError("Статус має бути додатним числом.")
        if new_status_id == current_status_id:
            raise DomainError("Замовлення вже має цей статус.")
        if user_id <= 0:
            raise DomainError("Не вказано користувача, який змінює статус.")
        return OrderStatusChange(order_id, current_status_id, new_status_id, user_id, now)


@dataclass(frozen=True)
class OrderDraftLine:
    """A line of a new order: product, chosen option (size) and the price the manager named."""

    product_id: int
    product_option_value_id: int | None
    quantity: int
    price: Decimal

    @property
    def total(self) -> Decimal:
        return self.price * self.quantity


@dataclass(frozen=True)
class OrderDraft:
    """
    An order a manager makes by hand (phone call, social networks). Checks what the old site's quick order form
    checked and counts the totals: products + shipping − discount.
    """

    lines: tuple[OrderDraftLine, ...]
    shipping_cost: Decimal
    discount: Decimal
    prepayment: Decimal
    """Advance the customer has already paid (the rest is, for example, cash on delivery)."""

    @property
    def sub_total(self) -> Decimal:
        return sum((line.total for line in self.lines), Decimal(0))

    @property
    def total(self) -> Decimal:
        return self.sub_total + self.shipping_cost - self.discount

    @staticmethod
    def create(
        firstname: str | None,
        telephone: str | None,
        shipping_method: str | None,
        payment_method: str | None,
        lines: list[OrderDraftLine],
        shipping_cost: Decimal,
        discount: Decimal,
        prepayment: Decimal,
    ) -> "OrderDraft":
        if not (firstname or "").strip() and not (telephone or "").strip():
            raise DomainError("Вкажіть ім'я або телефон клієнта.")
        if not (shipping_method or "").strip():
            raise DomainError("Необхідний спосіб доставки.")
        if not (payment_method or "").strip():
            raise DomainError("Необхідний спосіб оплати.")
        return OrderDraft.for_lines(lines, shipping_cost, discount, prepayment)

    @staticmethod
    def for_lines(
        lines: list[OrderDraftLine], shipping_cost: Decimal, discount: Decimal, prepayment: Decimal
    ) -> "OrderDraft":
        """Only the lines and sums: for a new order and for changing an existing one's composition."""
        if not lines:
            raise DomainError("Кошик не може бути порожнім.")
        if any(line.quantity <= 0 for line in lines):
            raise DomainError("Кількість товару має бути більшою за нуль.")
        if any(line.price < 0 for line in lines):
            raise DomainError("Ціна товару не може бути від'ємною.")
        if shipping_cost < 0 or discount < 0 or prepayment < 0:
            raise DomainError("Доставка, знижка й завдаток не можуть бути від'ємними.")
        draft = OrderDraft(tuple(lines), shipping_cost, discount, prepayment)
        if draft.total < 0:
            raise DomainError("Знижка не може перевищувати суму замовлення.")
        if prepayment > draft.total:
            raise DomainError("Сума завдатку не може перевищувати суму замовлення.")
        return draft


class CustomerRiskLevel(IntEnum):
    """Serialized as a number, like a .NET enum without a string converter."""

    NEW = 0  # no orders of this customer yet
    GREEN = 1  # orders, none cancelled
    YELLOW = 2  # less than half cancelled
    RED = 3  # half or more cancelled: ask for prepayment


CANCELLED_STATUS_ID = 19  # "Відмінено" on the real site
RISK_LOOKBACK_YEARS = 4


@dataclass(frozen=True)
class CustomerRisk:
    """How far a customer can be trusted, by their earlier orders (the old site's "Fraud Check")."""

    level: CustomerRiskLevel
    total_orders: int
    cancelled_order_ids: list[int]
    suspicion_rate: Decimal
    message: str

    @staticmethod
    def of(orders: list[tuple[int, int]]) -> "CustomerRisk":
        """orders: (order id, status id) pairs."""
        if not orders:
            return CustomerRisk(CustomerRiskLevel.NEW, 0, [], Decimal(0), "Новий клієнт. Замовлень не знайдено.")
        cancelled = [order_id for order_id, status_id in orders if status_id == CANCELLED_STATUS_ID]
        rate = (Decimal(len(cancelled) * 100) / len(orders)).quantize(Decimal("0.01"), ROUND_HALF_EVEN)
        if not cancelled:
            level, message = CustomerRiskLevel.GREEN, "Клієнт надійний. Скасувань немає."
        elif rate < 50:
            level, message = CustomerRiskLevel.YELLOW, "Увага: є скасовані замовлення. Рекомендовано перевірити."
        else:
            level, message = CustomerRiskLevel.RED, "Потенційний фрод-ризик. Рекомендовано вимагати передоплату."
        return CustomerRisk(level, len(orders), cancelled, rate, message)

    @staticmethod
    def phone_key(telephone: str | None) -> str | None:
        """Last 9 digits: "+38 (067) 123-45-67" and "0671234567" count as one number."""
        digits = re.sub(r"[^0-9]", "", telephone or "")
        return digits[-9:] if len(digits) >= 9 else None


def plain(value: Decimal) -> str:
    """A sum without extra zeros: 650, 650.5 (GiftVoucher.Plain in .NET)."""
    text = format(value.normalize(), "f")
    return text
