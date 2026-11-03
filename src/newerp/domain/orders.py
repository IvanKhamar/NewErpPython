"""Order rules: status changes, manual order drafts, customer risk."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import IntEnum, StrEnum
from math import ceil

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


# ── Different customers from one IP (the old site's "IP analysis" of the fraud check) ──

SHARED_IP_MIN_CLIENTS = 3  # from this many different customers on one address it is worth a look
SHARED_IP_LISTED = 10


@dataclass(frozen=True)
class SharedIp:
    ip: str
    unique_clients: int
    clients: tuple[str, ...]  # "Name Surname (phone)", up to SHARED_IP_LISTED


def shared_ip(ip: str, orders: Iterable[tuple[str, str, str]]) -> SharedIp | None:
    """
    orders: (first name, last name, phone) of the orders placed from this IP. Customers are told apart by the name
    and the phone digits, as the old site grouped them; a warning comes from SHARED_IP_MIN_CLIENTS different ones on.
    """
    clients: dict[tuple[str, str], str] = {}
    for first, last, phone in orders:
        name = f"{first.strip()} {last.strip()}".strip()
        key = (name.casefold(), re.sub(r"[^0-9]", "", phone))
        clients.setdefault(key, f"{name} ({phone.strip()})")
    if len(clients) < SHARED_IP_MIN_CLIENTS:
        return None
    return SharedIp(ip, len(clients), tuple(list(clients.values())[:SHARED_IP_LISTED]))


# ── Shipping checks (the old admin's check before a status change and its "discrepancies" button) ──

ACCEPTED_STATUS_ID = 18  # "Прийнято"
AWAITING_SHIPMENT_STATUS_ID = 21  # "Чекає на відправку": only pickups handed to the showroom
SHIPPED_STATUS_ID = 22  # "Відправлено"
URGENT_STATUS_ID = 23  # "Терміново"
# Domestic Nova Poshta: a branch, a parcel locker, the courier. Pickup, abroad and partners need no waybill.
NOVA_POSHTA_SHIPPING_CODES = (
    "novaposhta_warehouse.novaposhta_warehouse",
    "novaposhta_postomat.novaposhta_postomat",
    "novaposhta_address.novaposhta_address",
)
NOVA_POSHTA_DOOR_CODE = "novaposhta_address.novaposhta_address"
WAYBILL_STALE_HOURS = 4  # a waybill this old while the order is still "accepted" or "urgent" is a discrepancy
DISCREPANCY_DAYS = 3
DISCREPANCY_MAX_DAYS = 30


class IssueLevel(StrEnum):
    DANGER = "danger"  # the order cannot go like this
    WARNING = "warning"  # worth a look


@dataclass(frozen=True)
class ShippingIssue:
    level: IssueLevel
    text: str


def waybill_digits(number: str) -> str:
    return re.sub(r"[^0-9]", "", number)


def shipped_issues(shipping_code: str, telephone: str, waybill: str, sms_phones: list[str]) -> list[ShippingIssue]:
    """
    Before "Відправлено": a Nova Poshta order needs a waybill, and the SMS with its number must have gone to the
    order's phone. sms_phones: where the SMS with this waybill went since the order was placed.
    """
    if shipping_code not in NOVA_POSHTA_SHIPPING_CODES:
        return []
    if not waybill:
        return [ShippingIssue(IssueLevel.DANGER, "ТТН для цього замовлення не створена")]
    if not sms_phones:
        return [ShippingIssue(IssueLevel.WARNING, "SMS з номером ТТН клієнту не надсилалась")]
    order_phone = CustomerRisk.phone_key(telephone)
    if any(CustomerRisk.phone_key(phone) == order_phone for phone in sms_phones):
        return []
    others = ", ".join(dict.fromkeys(sms_phones))
    return [ShippingIssue(IssueLevel.DANGER, f"SMS з цією ТТН пішла на номер {others}, а в замовленні {telephone}")]


def pickup_issues(shipping_code: str, shipping_method: str, waybill: str) -> list[ShippingIssue]:
    """Before "Чекає на відправку", which is only for pickups handed to the showroom."""
    if waybill:
        return [
            ShippingIssue(
                IssueLevel.DANGER,
                "У замовлення є ТТН Нової Пошти. «Чекає на відправку» ставиться лише самовивозам, переданим у шоурум",
            )
        ]
    if shipping_code in NOVA_POSHTA_SHIPPING_CODES:
        return [
            ShippingIssue(
                IssueLevel.WARNING,
                f"Спосіб доставки в замовленні: {shipping_method}. Перевірте, чи це справді самовивіз",
            )
        ]
    return []


# ── Shipping cost of a new order (the old quick order asked Nova Poshta) ──


@dataclass(frozen=True)
class ParcelItem:
    """A product of the order with its catalog weight (kg) and size (cm), times the quantity."""

    quantity: int
    weight: Decimal
    length: Decimal
    width: Decimal
    height: Decimal


def parcel_weight(items: Iterable[ParcelItem]) -> Decimal:
    """In kg, at least 0.1 (Nova Poshta's minimum). The old site counted each product once; here times quantity."""
    return max(sum((i.weight * i.quantity for i in items), Decimal(0)), Decimal("0.1"))


def parcel_volume(items: Iterable[ParcelItem]) -> Decimal:
    """In cubic metres, each size rounded up to a whole centimetre like the old site."""
    cm3 = sum((Decimal(i.quantity * ceil(i.length) * ceil(i.width) * ceil(i.height)) for i in items), Decimal(0))
    return cm3 / 1_000_000
