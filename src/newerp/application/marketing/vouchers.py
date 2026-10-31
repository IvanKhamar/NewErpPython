"""Gift vouchers (dd_voucher of the old site): the list, a card with its spendings, and paying part of an order."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from newerp.application.common import Clock, Dto, LookupItem, Money, NotFoundError
from newerp.application.sms import SmsLog, SmsSender
from newerp.domain.common import DomainError, plain
from newerp.domain.marketing import (
    GiftVoucher,
    new_voucher_code,
    require_redeemable,
    voucher_balance,
    voucher_usable,
)
from newerp.domain.sms import SmsMessage, normalize_phone, require_text

CODE_ATTEMPTS = 20
DEFAULT_SMS_TEXT = (
    "Вам подарунковий сертифікат Vidro на {amount} грн. Код: {code}. Назвіть його менеджеру або введіть на сайті."
)


@dataclass(frozen=True)
class VoucherOptions:
    """Section Vouchers in .NET. A voucher bought in an order is usable when the order is in one of these statuses."""

    paid_status_ids: list[int] = field(default_factory=list)
    sms_text: str = DEFAULT_SMS_TEXT


@dataclass(frozen=True)
class VoucherRow:
    """A voucher with what was spent from it and the status of the order it was bought in (None: made in the admin)."""

    id: int
    order_id: int
    code: str
    from_name: str
    from_email: str
    to_name: str
    to_email: str
    theme_id: int
    theme_name: str
    message: str
    amount: Decimal
    active: bool
    created_at: datetime
    spent: Decimal
    order_status_id: int | None


@dataclass(frozen=True)
class OrderSums:
    total: Decimal
    prepayment: Decimal


@dataclass(frozen=True)
class SpentInOrder:
    voucher_id: int
    amount: Decimal


class SaveVoucherRequest(Dto):
    code: str | None = None
    from_name: str | None = None
    from_email: str | None = None
    to_name: str | None = None
    to_email: str | None = None
    theme_id: int | None = None
    message: str | None = None
    amount: Decimal | None = None
    active: bool | None = None


class SendVoucherSmsRequest(Dto):
    phone: str | None = None


class VoucherHistoryRow(Dto):
    order_id: int
    customer: str
    amount: Money
    date: datetime


class VoucherCardDto(Dto):
    id: int
    order_id: int
    code: str
    from_name: str
    from_email: str
    to_name: str
    to_email: str
    theme_id: int
    theme_name: str
    message: str
    amount: Money
    balance: Money
    active: bool
    usable: bool
    created_at: datetime
    history: list[VoucherHistoryRow]


class RedeemVoucherRequest(Dto):
    code: str | None = None
    amount: Decimal | None = None


class OrderVoucherDto(Dto):
    order_id: int
    voucher_id: int
    code: str
    amount: Money
    order_total: Money
    voucher_balance: Money


class SmsSendResultDto(Dto):
    sent: bool
    response: str


class VoucherStore(Protocol):
    async def list_rows(self, search: str) -> list[VoucherRow]: ...

    async def find(self, voucher_id: int) -> VoucherRow | None: ...

    async def find_by_code(self, code: str) -> VoucherRow | None: ...

    async def code_taken(self, code: str, except_id: int | None) -> bool: ...

    async def add(self, voucher: GiftVoucher, now: datetime) -> int: ...

    async def update(self, voucher_id: int, voucher: GiftVoucher) -> None: ...

    async def delete(self, voucher_id: int) -> None: ...

    async def history(self, voucher_id: int) -> list[VoucherHistoryRow]: ...

    async def themes(self) -> list[LookupItem]: ...

    async def order_sums(self, order_id: int) -> OrderSums | None: ...

    async def spent_in_order(self, order_id: int) -> SpentInOrder | None: ...

    async def redeem(self, order_id: int, voucher_id: int, code: str, amount: Decimal, now: datetime) -> None: ...

    async def release(self, order_id: int, voucher_id: int, amount: Decimal) -> None: ...


class VoucherService:
    def __init__(
        self, store: VoucherStore, sender: SmsSender, sms_log: SmsLog, options: VoucherOptions, clock: Clock
    ) -> None:
        self._store = store
        self._sender = sender
        self._sms_log = sms_log
        self._options = options
        self._clock = clock

    async def list_cards(self, search: str | None) -> list[VoucherCardDto]:
        return [self._card(row, []) for row in await self._store.list_rows((search or "").strip())]

    async def get(self, voucher_id: int) -> VoucherCardDto:
        return self._card(await self._require(voucher_id), await self._store.history(voucher_id))

    async def themes(self) -> list[LookupItem]:
        return await self._store.themes()

    async def create(self, request: SaveVoucherRequest) -> VoucherCardDto:
        """An empty code is generated (8 characters without 0/O and 1/I)."""
        code = request.code if request.code and request.code.strip() else await self._free_code()
        voucher = await self._validate(request, code, None)
        return await self.get(await self._store.add(voucher, self._clock.now()))

    async def update(self, voucher_id: int, request: SaveVoucherRequest) -> VoucherCardDto:
        await self._require(voucher_id)
        await self._store.update(voucher_id, await self._validate(request, request.code, voucher_id))
        return await self.get(voucher_id)

    async def delete(self, voucher_id: int) -> None:
        await self._require(voucher_id)
        if await self._store.history(voucher_id):
            raise DomainError("Сертифікатом уже платили. Вимкніть його замість видалення, щоб лишилась історія.")
        await self._store.delete(voucher_id)

    async def send_sms(self, voucher_id: int, phone: str | None) -> SmsSendResultDto:
        voucher = await self.get(voucher_id)
        if not voucher.usable or voucher.balance <= 0:
            raise DomainError(
                "Сертифікат зараз не можна використати (вимкнений, не оплачений або витрачений), тож SMS не надсилаємо."
            )
        number = normalize_phone(phone)
        text = require_text(
            self._options.sms_text.replace("{amount}", plain(voucher.balance, 2)).replace("{code}", voucher.code)
        )
        result = await self._sender.send(number, text)
        self._sms_log.add(SmsMessage(voucher.order_id, number, text, result.response, self._clock.now()))
        await self._sms_log.save_changes()
        return SmsSendResultDto(sent=result.sent, response=result.response)

    async def for_order(self, order_id: int) -> OrderVoucherDto | None:
        """The voucher spent in the order, None when there is none."""
        spent = await self._store.spent_in_order(order_id)
        if spent is None:
            return None
        voucher = await self.get(spent.voucher_id)
        sums = await self._store.order_sums(order_id)
        return OrderVoucherDto(
            order_id=order_id,
            voucher_id=voucher.id,
            code=voucher.code,
            amount=spent.amount,
            order_total=sums.total if sums else Decimal(0),
            voucher_balance=voucher.balance,
        )

    async def redeem(self, order_id: int, code: str | None, amount: Decimal | None) -> OrderVoucherDto:
        """Pays part of the order: a "voucher" total row, the voucher's history, a smaller order total."""
        order = await self._store.order_sums(order_id)
        if order is None:
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")
        if await self._store.spent_in_order(order_id) is not None:
            raise DomainError("За замовленням уже списано сертифікат. Щоб змінити суму, спершу зніміть його.")
        clean = (code or "").strip().upper()
        row = await self._store.find_by_code(clean)
        if row is None:
            raise NotFoundError(f"Сертифіката з кодом {clean} немає.")
        voucher = self._card(row, [])
        left = max(order.total - order.prepayment, Decimal(0))
        spend = amount if amount is not None else min(voucher.balance, left)
        require_redeemable(spend, voucher.balance, left, voucher.usable)
        await self._store.redeem(order_id, voucher.id, voucher.code, spend, self._clock.now())
        result = await self.for_order(order_id)
        assert result is not None
        return result

    async def release(self, order_id: int) -> None:
        """Takes the voucher off the order: the money goes back to it, the order total is as it was."""
        spent = await self._store.spent_in_order(order_id)
        if spent is None:
            raise NotFoundError(f"За замовленням {order_id} сертифікат не списано.")
        await self._store.release(order_id, spent.voucher_id, spent.amount)

    async def _validate(self, r: SaveVoucherRequest, code: str | None, except_id: int | None) -> GiftVoucher:
        voucher = GiftVoucher.of(
            code, r.from_name, r.from_email, r.to_name, r.to_email, r.theme_id, r.message, r.amount, r.active
        )
        if await self._store.code_taken(voucher.code, except_id):
            raise DomainError(f"Сертифікат з кодом {voucher.code} уже є.")
        return voucher

    async def _free_code(self) -> str:
        for _ in range(CODE_ATTEMPTS):
            code = new_voucher_code()
            if not await self._store.code_taken(code, None):
                return code
        raise RuntimeError("Не вдалося підібрати вільний код сертифіката.")

    async def _require(self, voucher_id: int) -> VoucherRow:
        row = await self._store.find(voucher_id)
        if row is None:
            raise NotFoundError(f"Сертифікат {voucher_id} не знайдено.")
        return row

    def _card(self, r: VoucherRow, history: list[VoucherHistoryRow]) -> VoucherCardDto:
        paid = r.order_status_id is not None and r.order_status_id in self._options.paid_status_ids
        return VoucherCardDto(
            id=r.id,
            order_id=r.order_id,
            code=r.code,
            from_name=r.from_name,
            from_email=r.from_email,
            to_name=r.to_name,
            to_email=r.to_email,
            theme_id=r.theme_id,
            theme_name=r.theme_name,
            message=r.message,
            amount=r.amount,
            balance=voucher_balance(r.amount, r.spent),
            active=r.active,
            usable=voucher_usable(r.active, r.order_id, paid),
            created_at=r.created_at,
            history=history,
        )
