"""SMS to an order's customer through SMSClub, written to the order's SMS log."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from newerp.application.common import Clock, Dto, NotFoundError
from newerp.domain.sms import SmsMessage, find_ttn_numbers, normalize_phone, require_text


class SendSmsRequest(Dto):
    """force: send even if the text has a waybill of another order."""

    text: str | None = None
    force: bool = False


class SmsDto(Dto):
    id: int
    order_id: int
    phone: str
    text: str
    response: str
    sent_at: datetime


class TtnMismatchDto(Dto):
    """The text names the waybill of another order: probably a mistake, so the manager confirms."""

    ttn: str
    ttn_order_id: int


class SendSmsResultDto(Dto):
    sent: bool
    message: SmsDto | None
    ttn_mismatch: TtnMismatchDto | None = None


class SmsSettingsDto(Dto):
    """is_configured False: the stub works, nothing is really sent."""

    is_configured: bool


@dataclass(frozen=True)
class SmsSendResult:
    sent: bool
    response: str  # the service's answer as is, kept in the log


class SmsSender(Protocol):
    @property
    def is_configured(self) -> bool: ...

    async def send(self, phone: str, text: str) -> SmsSendResult: ...


class SmsLog(Protocol):
    async def get_order_phone(self, order_id: int) -> str | None:
        """None if there is no such order."""
        ...

    async def list_for_order(self, order_id: int) -> list[SmsDto]: ...

    async def find_order_ids_by_ttn(self, numbers: list[str]) -> dict[str, int]: ...

    def add(self, message: SmsMessage) -> None: ...

    async def save_changes(self) -> None: ...


class SmsService:
    def __init__(self, log: SmsLog, sender: SmsSender, clock: Clock) -> None:
        self._log = log
        self._sender = sender
        self._clock = clock

    async def send_to_order(self, order_id: int, request: SendSmsRequest) -> SendSmsResultDto:
        """Sends and logs the SMS, even when the service refused (the answer is in the log)."""
        raw_phone = await self._log.get_order_phone(order_id)
        if raw_phone is None:
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")
        text = require_text(request.text)
        phone = normalize_phone(raw_phone)
        if not request.force:
            owners = await self._log.find_order_ids_by_ttn(find_ttn_numbers(text))
            foreign = next(((ttn, owner) for ttn, owner in owners.items() if owner != order_id), None)
            if foreign is not None:
                mismatch = TtnMismatchDto(ttn=foreign[0], ttn_order_id=foreign[1])
                return SendSmsResultDto(sent=False, message=None, ttn_mismatch=mismatch)

        result = await self._sender.send(phone, text)
        message = SmsMessage(order_id, phone, text, result.response, self._clock.now())
        self._log.add(message)
        await self._log.save_changes()
        dto = SmsDto(
            id=message.id, order_id=order_id, phone=phone, text=text, response=message.response, sent_at=message.sent_at
        )
        return SendSmsResultDto(sent=result.sent, message=dto)

    async def history(self, order_id: int) -> list[SmsDto]:
        return await self._log.list_for_order(order_id)
