"""Marketing rules of the old site: birthdays and their coupons, the contacts export, gift vouchers."""

import calendar
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import IntEnum, IntFlag

from newerp.domain.common import DomainError, plain
from newerp.domain.sms import normalize_phone

# ═══ Birthdays ═══


class BirthdayPeriod(IntEnum):
    TODAY = 0
    TOMORROW = 1
    WEEK = 2  # the next 7 days, today included
    MONTH = 3  # this calendar month
    ALL = 4


def next_birthday(day: int, month: int, today: date) -> date | None:
    """The next birthday from today on; None for an impossible date. 29.02 falls on 28.02 in common years."""
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    if day > calendar.monthrange(2024, month)[1]:  # 2024 is a leap year: so 29.02 counts as a possible date
        return None
    this_year = _on_year(day, month, today.year)
    return this_year if this_year >= today else _on_year(day, month, today.year + 1)


def in_period(day: int, month: int, period: BirthdayPeriod, today: date) -> bool:
    upcoming = next_birthday(day, month, today)
    if upcoming is None:
        return False
    match period:
        case BirthdayPeriod.TODAY:
            return upcoming == today
        case BirthdayPeriod.TOMORROW:
            return upcoming == today + timedelta(days=1)
        case BirthdayPeriod.WEEK:
            return upcoming <= today + timedelta(days=7)
        case BirthdayPeriod.MONTH:
            return month == today.month
    return True


def _on_year(day: int, month: int, year: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


COUPON_PREFIX = "DN"


def new_coupon_code() -> str:
    """DN + 8 hex digits, like the old site's birthday coupons."""
    return COUPON_PREFIX + secrets.token_hex(4).upper()


def require_coupon_code(code: str | None) -> str:
    trimmed = (code or "").strip().upper()
    if not 4 <= len(trimmed) <= 20 or not all(c.isascii() and c.isalnum() for c in trimmed):
        raise DomainError("Код купона — від 4 до 20 латинських літер і цифр.")
    return trimmed


# ═══ Contacts export ═══


class ContactField(IntEnum):
    PHONE = 0
    EMAIL = 1


class ContactSource(IntFlag):
    """Where contacts come from; IntFlag combines them with |, like a [Flags] enum."""

    NONE = 0
    ORDERS = 1
    QUESTIONS = 2  # "ask about a product": a phone only
    SUBSCRIBERS = 4  # newsletter: an e-mail only


@dataclass(frozen=True)
class OrderContact:
    phone: str
    email: str
    first_name: str
    last_name: str
    city: str
    date_added: datetime


@dataclass(frozen=True)
class ContactRow:
    first_name: str
    last_name: str
    city: str
    phone: str
    email: str


def clean_phone(phone: str | None) -> str | None:
    if not phone or not phone.strip():
        return None
    try:
        return normalize_phone(phone)
    except DomainError:
        return None


_EMAIL = re.compile(r'^[^@\s<>()\[\],;:"]+@[^@\s<>()\[\],;:"]+$')


def clean_email(email: str | None) -> str | None:
    trimmed = (email or "").strip().lower()
    if not trimmed or not _EMAIL.match(trimmed) or "." not in trimmed:
        return None
    return trimmed


def build_contacts(field: ContactField, values: Iterable[str], orders: Iterable[OrderContact]) -> list[ContactRow]:
    """Unique clean values in ordinal order; name and city come from the latest order with the same value."""
    clean = clean_phone if field == ContactField.PHONE else clean_email
    latest: dict[str, OrderContact] = {}
    for order in orders:
        key = clean(order.phone if field == ContactField.PHONE else order.email)
        if key is not None and (key not in latest or order.date_added > latest[key].date_added):
            latest[key] = order
    rows = []
    for value in sorted({v for v in map(clean, values) if v is not None}):
        order = latest.get(value)
        first, last, city = (
            (order.first_name.strip(), order.last_name.strip(), order.city.strip()) if order else ("", "", "")
        )
        if field == ContactField.PHONE:
            rows.append(ContactRow(first, last, city, value, (clean_email(order.email) if order else None) or ""))
        else:
            rows.append(ContactRow(first, last, city, (clean_phone(order.phone) if order else None) or "", value))
    return rows


def contacts_csv(field: ContactField, rows: Iterable[ContactRow]) -> str:
    main, second = ("Телефон", "E-mail") if field == ContactField.PHONE else ("E-mail", "Телефон")
    lines = [f"Ім'я;Прізвище;Місто;{main};{second}"]
    for row in rows:
        values = (row.phone, row.email) if field == ContactField.PHONE else (row.email, row.phone)
        lines.append(";".join(_escape(v) for v in (row.first_name, row.last_name, row.city, *values)))
    return "".join(line + "\r\n" for line in lines)


def _escape(value: str) -> str:
    """Quotes for fields with ; " or a line break, and an apostrophe before =+-@, so Excel does not run a formula."""
    if value and value[0] in "=+-@":
        value = "'" + value
    if any(c in value for c in ';"\r\n'):
        return '"' + value.replace('"', '""') + '"'
    return value


# ═══ Gift vouchers ═══

VOUCHER_CODE_MAX_LENGTH = 10
# Generated codes: 8 characters without the look-alikes 0/O and 1/I, easy to dictate on the phone.
_VOUCHER_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass(frozen=True)
class GiftVoucher:
    code: str
    from_name: str
    from_email: str
    to_name: str
    to_email: str
    theme_id: int
    message: str
    amount: Decimal
    active: bool

    @staticmethod
    def of(
        code: str | None,
        from_name: str | None,
        from_email: str | None,
        to_name: str | None,
        to_email: str | None,
        theme_id: int | None,
        message: str | None,
        amount: Decimal | None,
        active: bool | None,
    ) -> "GiftVoucher":
        clean_code = (code or "").strip().upper()
        if not 1 <= len(clean_code) <= VOUCHER_CODE_MAX_LENGTH or not all(
            c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" for c in clean_code
        ):
            raise DomainError(f"Код сертифіката — від 1 до {VOUCHER_CODE_MAX_LENGTH} латинських літер або цифр.")
        if not _is_money(amount):
            raise DomainError("Сума сертифіката — більше 0, не більше двох знаків після коми.")
        if not to_name or not to_name.strip():
            raise DomainError("Вкажіть, кому сертифікат.")
        clean_to, clean_from = to_name.strip(), _text(from_name)
        # dd_voucher: names varchar(64), e-mails varchar(96).
        if len(clean_to) > 64 or len(clean_from) > 64 or len(_text(from_email)) > 96 or len(_text(to_email)) > 96:
            raise DomainError("Ім'я — не довше 64 символів, email — не довше 96.")
        return GiftVoucher(
            clean_code, clean_from, _text(from_email), clean_to, _text(to_email), theme_id or 0, _text(message),
            amount, True if active is None else active,
        )  # fmt: skip


def new_voucher_code() -> str:
    return "".join(secrets.choice(_VOUCHER_ALPHABET) for _ in range(8))


def voucher_balance(amount: Decimal, spent: Decimal | None) -> Decimal:
    """spent is the sum of dd_voucher_history, negative for spendings."""
    return amount + (spent or 0)


def voucher_usable(active: bool, order_id: int, order_paid: bool) -> bool:
    """A voucher bought in an order is usable only once that order is paid."""
    return active and (order_id == 0 or order_paid)


def require_redeemable(amount: Decimal | None, balance: Decimal, left: Decimal, usable: bool) -> None:
    if not usable:
        raise DomainError(
            "Сертифікат зараз не можна використати: він вимкнений або куплений у ще не оплаченому замовленні."
        )
    if not _is_money(amount):
        raise DomainError("Сума списання — більше 0, не більше двох знаків після коми.")
    if amount > balance:
        raise DomainError(f"На сертифікаті лише {plain(balance)} грн.")
    if amount > left:
        raise DomainError(f"Це більше, ніж лишилось сплатити за замовлення ({plain(left)} грн).")


def _is_money(amount: Decimal | None) -> bool:
    return amount is not None and amount > 0 and amount.quantize(Decimal("0.01")) == amount


def _text(value: str | None) -> str:
    return (value or "").strip()
