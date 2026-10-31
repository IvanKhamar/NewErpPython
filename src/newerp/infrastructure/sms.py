"""The SMS log (dd_sms_log) and the SMSClub sender."""

import json
import logging

import httpx
from sqlalchemy import Column, DateTime, Integer, String, Table, Text, select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.sms import SmsDto, SmsSendResult
from newerp.config import SmsClubSettings
from newerp.domain.sms import SmsMessage
from newerp.infrastructure.db import Base
from newerp.infrastructure.delivery.mapping import shipment_documents
from newerp.infrastructure.models.orders import DdOrder

log = logging.getLogger(__name__)

sms_log = Table(
    "dd_sms_log",
    Base.metadata,
    Column("message_id", Integer, primary_key=True),
    Column("order_id", Integer),
    Column("pre_order_id", Integer),
    Column("phone", String(32)),
    Column("text", Text),
    Column("response", Text),
    Column("date", DateTime),
)

Base.registry.map_imperatively(SmsMessage, sms_log, properties={"id": sms_log.c.message_id, "sent_at": sms_log.c.date})


class SqlSmsLog:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_order_phone(self, order_id: int) -> str | None:
        return await self._session.scalar(select(DdOrder.telephone).where(DdOrder.order_id == order_id))

    async def list_for_order(self, order_id: int) -> list[SmsDto]:
        rows = await self._session.scalars(
            select(SmsMessage)
            .where(sms_log.c.order_id == order_id)
            .order_by(sms_log.c.date.desc(), sms_log.c.message_id.desc())
        )
        return [
            SmsDto(id=m.id, order_id=m.order_id, phone=m.phone, text=m.text, response=m.response, sent_at=m.sent_at)
            for m in rows
        ]

    async def find_order_ids_by_ttn(self, numbers: list[str]) -> dict[str, int]:
        if not numbers:
            return {}
        d = shipment_documents.c
        rows = await self._session.execute(
            select(d.number, d.order_id).where(d.number.in_(numbers)).order_by(d.id.desc())
        )
        owners: dict[str, int] = {}
        for number, order_id in rows:
            owners.setdefault(number, order_id)  # as the old site: a repeated number takes the latest record
        return owners

    def add(self, message: SmsMessage) -> None:
        self._session.add(message)

    async def save_changes(self) -> None:
        await self._session.commit()


NOT_ALLOWED_RESPONSE = "НЕ НАДІСЛАНО: номера немає в SmsClub:OnlyPhones"
STUB_RESPONSE = "НЕ НАДІСЛАНО: заглушка NewErp, справжній SMS-сервіс не підключено"


class SmsClubSender:
    """SMSClub JSON API with a Bearer token. only_phones (testing on real numbers): send to these only."""

    def __init__(self, settings: SmsClubSettings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    is_configured = True

    async def send(self, phone: str, text: str) -> SmsSendResult:
        if self._settings.only_phones and phone not in self._settings.only_phones:
            return SmsSendResult(sent=False, response=NOT_ALLOWED_RESPONSE)
        body = {"phone": [phone], "message": text, "src_addr": self._settings.sender}
        headers = {"Authorization": f"Bearer {self._settings.token.get_secret_value()}"}
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as http:
                response = await http.post(
                    f"{self._settings.base_url.rstrip('/')}/sms/send", json=body, headers=headers
                )
        except httpx.HTTPError as error:
            return SmsSendResult(sent=False, response=f"НЕ НАДІСЛАНО: SMSClub недоступний ({error})")
        answer = response.text
        return SmsSendResult(
            sent=response.is_success and is_accepted(answer, phone),
            response=answer if answer else f"HTTP {response.status_code}",
        )


def is_accepted(body: str, phone: str) -> bool:
    """SMSClub lists the accepted numbers in success_request.info as {"<sms id>": "<phone>"}."""
    try:
        info = json.loads(body)["success_request"]["info"]
    except (ValueError, KeyError, TypeError):
        return False
    return isinstance(info, dict) and phone in info.values()


class StubSmsSender:
    """Sends nothing; the attempt still gets into the order's SMS log."""

    is_configured = False

    async def send(self, phone: str, text: str) -> SmsSendResult:
        log.info("SMS stub: nothing sent to %s, a message of %d characters", phone, len(text))
        return SmsSendResult(sent=False, response=STUB_RESPONSE)


def sms_sender(settings: SmsClubSettings) -> SmsClubSender | StubSmsSender:
    return SmsClubSender(settings) if settings.token.get_secret_value() else StubSmsSender()
