"""Birthday greetings (with a one-time coupon), bulk SMS, a customer's orders and the contacts export."""

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from pydantic import Field

from newerp.application.common import Clock, Dto, Money, ukrainian_key
from newerp.application.sms import SmsLog, SmsSender, SmsSendResult
from newerp.domain.common import DomainError
from newerp.domain.marketing import (
    BirthdayPeriod,
    ContactField,
    ContactSource,
    OrderContact,
    build_contacts,
    clean_phone,
    contacts_csv,
    in_period,
    new_coupon_code,
    next_birthday,
    require_coupon_code,
)
from newerp.domain.sms import SmsMessage, normalize_phone, require_text

MAX_BULK_PHONES = 500


@dataclass(frozen=True)
class MarketingOptions:
    """Section Marketing in .NET."""

    birthday_discount_percent: Decimal = Decimal(20)
    birthday_coupon_days: int = 30
    pickup_city: str = "Львів"  # the city of an order picked up in the shop
    pickup_method_marker: str = "амовив"  # part of "Самовивіз" in the shipping method
    bulk_sms_delay_milliseconds: int = 300  # a pause between bulk SMS, so the service does not block them


class BirthdayContactDto(Dto):
    phone: str
    first_name: str
    last_name: str
    email: str
    city: str
    instagram: str
    day: int
    month: int
    next_birthday: date
    sent_this_year: int


class BirthdaySmsRequest(Dto):
    phone: str | None = None
    text: str | None = None
    coupon_code: str | None = None


class BirthdaySmsResult(Dto):
    sent: bool
    coupon_id: int | None
    response: str


class BulkSmsRequest(Dto):
    phones: list[str] | None = None
    text: str | None = None


class BulkSmsResult(Dto):
    sent: int
    not_sent: int
    skipped: list[str]


class CouponCodeDto(Dto):
    code: str


class CustomerOrderDto(Dto):
    order_id: int
    date_added: datetime
    total: Money
    payment_method: str
    status: str
    products: str


class ContactExportRequest(Dto):
    field: ContactField
    sources: ContactSource
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None


@dataclass(frozen=True)
class BirthdayOrder:
    phone: str
    first_name: str
    last_name: str
    email: str
    shipping_city: str
    shipping_method: str
    instagram: str
    day: int
    month: int
    date_added: datetime


@dataclass(frozen=True)
class CouponDraft:
    code: str
    name: str
    discount_percent: Decimal
    start: date
    end: date
    created_at: datetime


class MarketingStore(Protocol):
    async def birthday_orders(self) -> list[BirthdayOrder]: ...

    async def birthday_sms_phones(self, since: datetime) -> list[str]: ...

    async def coupon_exists(self, code: str) -> bool: ...

    async def add_coupon(self, coupon: CouponDraft) -> int: ...

    async def order_phones(self) -> list[tuple[int, str]]: ...

    async def orders(self, order_ids: list[int]) -> list[CustomerOrderDto]: ...

    async def contact_values(self, request: ContactExportRequest) -> list[str]: ...

    async def order_contacts(self) -> list[OrderContact]: ...


class MarketingService:
    def __init__(
        self, store: MarketingStore, sender: SmsSender, sms_log: SmsLog, options: MarketingOptions, clock: Clock
    ) -> None:
        self._store = store
        self._sender = sender
        self._sms_log = sms_log
        self._options = options
        self._clock = clock

    async def birthdays(self, period: BirthdayPeriod) -> list[BirthdayContactDto]:
        """One row per phone, from its latest order, with how many greetings it got this year."""
        now = self._clock.now()
        today = now.date()
        phones = await self._store.birthday_sms_phones(datetime(now.year, 1, 1))
        sent = Counter(phone for phone in map(clean_phone, phones) if phone)
        latest: dict[str, BirthdayOrder] = {}
        for order in await self._store.birthday_orders():
            phone = clean_phone(order.phone)
            if phone is not None and (phone not in latest or order.date_added > latest[phone].date_added):
                latest[phone] = order
        contacts = [
            BirthdayContactDto(
                phone=phone,
                first_name=o.first_name.strip(),
                last_name=o.last_name.strip(),
                email=o.email,
                city=self._city(o),
                instagram=o.instagram,
                day=o.day,
                month=o.month,
                next_birthday=next_birthday(o.day, o.month, today),
                sent_this_year=sent[phone],
            )
            for phone, o in latest.items()
            if in_period(o.day, o.month, period, today)
        ]

        def key(c: BirthdayContactDto):
            first = c.next_birthday.toordinal() if period == BirthdayPeriod.WEEK else c.month * 100 + c.day
            return first, ukrainian_key(c.last_name), ukrainian_key(c.first_name)

        return sorted(contacts, key=key)

    async def new_coupon_code(self) -> CouponCodeDto:
        """A DN… code that is not taken yet (the coupon itself is made together with the SMS)."""
        code = new_coupon_code()
        while await self._store.coupon_exists(code):
            code = new_coupon_code()
        return CouponCodeDto(code=code)

    async def send_birthday_sms(self, request: BirthdaySmsRequest) -> BirthdaySmsResult:
        """A greeting to one customer; with a code, first a coupon with a discount for birthday_coupon_days days."""
        phone = normalize_phone(request.phone)
        text = require_text(request.text)
        now = self._clock.now()
        coupon_id = None
        if request.coupon_code and request.coupon_code.strip():
            code = require_coupon_code(request.coupon_code)
            if await self._store.coupon_exists(code):
                raise DomainError(f"Купон {code} уже є, згенеруй новий код.")
            start = now.date()
            coupon_id = await self._store.add_coupon(
                CouponDraft(
                    code,
                    f"ДН {phone} {now:%d.%m.%Y}",
                    self._options.birthday_discount_percent,
                    start,
                    start + timedelta(days=self._options.birthday_coupon_days),
                    now,
                )  # fmt: skip
            )
        result = await self._send(phone, text, now)
        return BirthdaySmsResult(sent=result.sent, coupon_id=coupon_id, response=result.response)

    async def send_bulk(self, request: BulkSmsRequest) -> BulkSmsResult:
        """The same text to many numbers (without coupons), each number once."""
        text = require_text(request.text)
        phones = request.phones or []
        if not phones:
            raise DomainError("Немає одержувачів.")
        if len(phones) > MAX_BULK_PHONES:
            raise DomainError(f"За раз — не більше {MAX_BULK_PHONES} одержувачів.")
        cleaned = [(raw, clean_phone(raw)) for raw in phones]
        sent = not_sent = 0
        for phone in dict.fromkeys(clean for _, clean in cleaned if clean):
            if sent + not_sent > 0 and self._options.bulk_sms_delay_milliseconds > 0:
                await asyncio.sleep(self._options.bulk_sms_delay_milliseconds / 1000)
            if (await self._send(phone, text, self._clock.now())).sent:
                sent += 1
            else:
                not_sent += 1
        return BulkSmsResult(sent=sent, not_sent=not_sent, skipped=[raw for raw, clean in cleaned if clean is None])

    async def customer_orders(self, phone: str | None) -> list[CustomerOrderDto]:
        clean = normalize_phone(phone)
        ids = [order_id for order_id, p in await self._store.order_phones() if clean_phone(p) == clean]
        return await self._store.orders(ids) if ids else []

    async def export_csv(self, request: ContactExportRequest) -> str:
        if request.sources == ContactSource.NONE:
            raise DomainError("Вибери, звідки брати контакти.")
        if request.from_ is not None and request.to is not None and request.from_ > request.to:
            raise DomainError("Початок періоду пізніший за кінець.")
        values = await self._store.contact_values(request)
        rows = build_contacts(request.field, values, await self._store.order_contacts())
        return contacts_csv(request.field, rows)

    async def _send(self, phone: str, text: str, now: datetime) -> SmsSendResult:
        result = await self._sender.send(phone, text)
        self._sms_log.add(SmsMessage.birthday(phone, text, result.response, now))
        await self._sms_log.save_changes()
        return result

    def _city(self, order: BirthdayOrder) -> str:
        if order.shipping_city.strip():
            return order.shipping_city.strip()
        if self._options.pickup_method_marker.casefold() in order.shipping_method.casefold():
            return self._options.pickup_city
        return ""
