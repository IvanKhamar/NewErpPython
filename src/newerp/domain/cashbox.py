"""The Checkbox cash register (software RRO), from the old site's cashbox module: cashiers, shifts, fiscal receipts."""

import html
import re
from dataclasses import dataclass
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from newerp.domain.common import DomainError


class Cashier:
    """
    A Checkbox cashier (dd_cashbox_user of the old site). Login, password and license key are not here: NewErp
    keeps them only in the settings (env vars), not in the database.
    """

    def __init__(
        self, name: str | None, description: str | None, payment_code: str | None, auto_close_time: time | None,
        warehouse_id: int, requisites_id: int, is_active: bool, created_at: datetime,
    ) -> None:  # fmt: skip
        self.id: int | None = None
        self.date_added = created_at
        self.update(name, description, payment_code, auto_close_time, warehouse_id, requisites_id, is_active)

    def update(
        self, name: str | None, description: str | None, payment_code: str | None, auto_close_time: time | None,
        warehouse_id: int, requisites_id: int, is_active: bool,
    ) -> None:  # fmt: skip
        if not name or not name.strip():
            raise DomainError("Вкажіть ім'я касира: воно друкується в чеку.")
        self.name = name.strip()  # printed on the receipt
        self.description = (description or "").strip()
        # The OpenCart payment code (cod, liqpay...) whose orders get this cashier. Empty: the default cashier.
        self.payment_code = (payment_code or "").strip()
        self.auto_close_time = auto_close_time  # when to close the shift automatically (Z report); None: never
        self.warehouse_id = warehouse_id
        self.requisites_id = requisites_id
        self.is_active = is_active


class CashboxDirectoryEntry:
    """A cash register directory entry with a name and description: a point of sale or a sole proprietor."""

    def __init__(self, name: str | None, description: str | None, created_at: datetime) -> None:
        self.id: int | None = None
        self.date_added = created_at
        self.rename(name, description)

    def rename(self, name: str | None, description: str | None) -> None:
        if not name or not name.strip():
            raise DomainError("Назва обов'язкова.")
        self.name = name.strip()
        self.description = (description or "").strip()


class CashboxWarehouse(CashboxDirectoryEntry):
    """A point of sale of the cash register (dd_cashbox_warehouse)."""


class CashboxRequisites(CashboxDirectoryEntry):
    """The sole proprietor the receipt is issued for (dd_cashbox_requisites)."""


class WorkShift:
    """A cashier's shift (dd_cashbox_work_shift). Receipts can be issued only in an open shift."""

    def __init__(self, cashier_id: int, checkbox_shift_id: str, opened_by_user_id: int, opened_at: datetime) -> None:
        self.id: int | None = None
        self.cashier_id = cashier_id
        self.checkbox_shift_id = checkbox_shift_id
        self.user_id = opened_by_user_id
        self.date_added = opened_at
        self.date_start = opened_at
        self.date_end: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.date_end is None

    def close(self, closed_at: datetime) -> None:
        if self.date_end is None:
            self.date_end = closed_at


class ZReport:
    """A Z report: the shift is closed (dd_cashbox_report). user_id 0: closed automatically."""

    def __init__(self, cashier_id: int, checkbox_report_id: str, user_id: int, created_at: datetime) -> None:
        self.id: int | None = None
        self.cashier_id = cashier_id
        self.checkbox_report_id = checkbox_report_id
        self.user_id = user_id
        self.date_added = created_at


class Cheque:
    """A fiscal receipt of an order (dd_cashbox_cheque). One receipt per order, as on the old site."""

    def __init__(self, order_id: int, cashier: Cashier, total: Decimal, work_shift_id: int, created_at: datetime):
        self.id: int | None = None
        self.order_id = order_id
        self.cashier_id = cashier.id
        self.warehouse_id = cashier.warehouse_id
        self.requisites_id = cashier.requisites_id
        self.sum = total  # to pay: order total minus the advance
        self.checkbox_id = ""  # the receipt id in Checkbox; empty: not fiscalized yet
        self.work_shift_id = work_shift_id  # 0: unknown (an old site's receipt)
        self.report_id = 0  # the Z report that closed the shift; 0: still open
        self.date_added = created_at

    @property
    def is_fiscalized(self) -> bool:
        return self.checkbox_id != ""

    def mark_fiscalized(self, checkbox_id: str, work_shift_id: int) -> None:
        self.checkbox_id = checkbox_id
        self.work_shift_id = work_shift_id


class ReceiptPaymentType(Enum):
    """Checkbox tells only cash from cashless."""

    CASH = "CASH"
    CASHLESS = "CASHLESS"


@dataclass(frozen=True)
class ReceiptSourceLine:
    product_id: int
    name: str
    price: Decimal
    quantity: int


@dataclass(frozen=True)
class ReceiptGood:
    """Money in kopecks, quantity in thousandths (as the Checkbox API takes them)."""

    code: str
    name: str
    price_kopecks: int
    quantity_thousandths: int


@dataclass(frozen=True)
class ReceiptPayment:
    type: ReceiptPaymentType
    label: str
    value_kopecks: int


# OpenCart payment codes Checkbox must see as cashless; the rest are cash (as on the old site).
CASHLESS_PAYMENT_CODES = frozenset({"wayforpay", "liqpay", "mono", "bank_transfer", "paid", "paid_shipping", "cod"})
CASH_ON_DELIVERY_CODE = "cod"


@dataclass(frozen=True)
class FiscalReceipt:
    """A sale receipt for Checkbox (POST /api/v1/receipts/sell)."""

    cashier_name: str
    goods: list[ReceiptGood]
    payment: ReceiptPayment
    discount_kopecks: int
    footer: str
    email: str | None

    @staticmethod
    def build(
        order_id: int, cashier_name: str, lines: list[ReceiptSourceLine], total: Decimal, payment_code: str,
        payment_method: str, email: str | None,
    ) -> "FiscalReceipt":  # fmt: skip
        """
        As the old site: products with a price (zero ones are skipped), one payment for the sum to pay, and a
        discount for the difference if the products cost more (a coupon, the advance).
        """
        goods = [
            ReceiptGood(str(line.product_id), line.name.strip(), _kopecks(line.price), line.quantity * 1000)
            for line in lines
            if line.price != 0
        ]
        if not goods:
            raise DomainError("У замовленні немає товарів з ціною, чек видати нема з чого.")
        to_pay = _kopecks(total)
        if to_pay <= 0:
            raise DomainError(
                "Сума до оплати нульова (можливо, замовлення повністю оплачене завдатком), чек не потрібен."
            )
        goods_total = sum(g.price_kopecks * g.quantity_thousandths // 1000 for g in goods)
        # The old site sent such a receipt as is, and Checkbox refused it. Here it is explained at once.
        if to_pay > goods_total:
            raise DomainError(
                f"Сума до оплати ({total:.2f}) більша за суму товарів ({Decimal(goods_total) / 100:.2f}): мабуть, "
                "у замовленні є платна доставка. Checkbox такий чек не прийме."
            )
        kind = ReceiptPaymentType.CASHLESS if payment_code in CASHLESS_PAYMENT_CODES else ReceiptPaymentType.CASH
        label = "Оплата в Nova pay" if payment_code == CASH_ON_DELIVERY_CODE else _plain_text(payment_method)
        if not label:
            label = "Готівка" if kind == ReceiptPaymentType.CASH else "Безготівкова оплата"
        return FiscalReceipt(
            cashier_name, goods, ReceiptPayment(kind, label, to_pay), goods_total - to_pay, f"Замовлення № {order_id}",
            email.strip() if email and email.strip() else None,
        )  # fmt: skip


def _kopecks(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal(1), ROUND_HALF_UP))


def _plain_text(markup: str | None) -> str:
    """An OpenCart payment method name may hold HTML (icons, &quot;): only the text goes to the receipt."""
    text = re.sub(r"<[^>]*>", "", html.unescape(markup or ""))
    return re.sub(r"\s+", " ", text).strip()
