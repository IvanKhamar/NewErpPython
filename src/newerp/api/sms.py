"""SMS to an order's customer (part of the orders module in .NET; access as to orders)."""

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends

from newerp.api.deps import ClockDep, Session
from newerp.api.security import Policies, require_roles
from newerp.application.sms import SendSmsRequest, SendSmsResultDto, SmsDto, SmsService, SmsSettingsDto
from newerp.config import get_settings
from newerp.infrastructure.sms import SqlSmsLog, sms_sender

orders_access = [Depends(require_roles(*Policies.ORDERS))]
router = APIRouter(prefix="/orders/{order_id}/sms", tags=["Замовлення"], dependencies=orders_access)
settings_router = APIRouter(tags=["Замовлення"], dependencies=orders_access)


@lru_cache
def get_sender():
    return sms_sender(get_settings().sms_club)


def get_service(session: Session, clock: ClockDep) -> SmsService:
    return SmsService(SqlSmsLog(session), get_sender(), clock)


Service = Annotated[SmsService, Depends(get_service)]


@router.post("", summary="Надіслати SMS клієнту (без токена — заглушка: sent = false, але запис у журналі)")
async def send(order_id: int, request: SendSmsRequest, service: Service) -> SendSmsResultDto:
    return await service.send_to_order(order_id, request)


@router.get("", summary="SMS, надіслані клієнту цього замовлення")
async def history(order_id: int, service: Service) -> list[SmsDto]:
    return await service.history(order_id)


@settings_router.get("/sms/settings", summary="Чи підключено справжній SMS-сервіс (false — працює заглушка)")
async def sms_settings() -> SmsSettingsDto:
    return SmsSettingsDto(is_configured=get_sender().is_configured)
