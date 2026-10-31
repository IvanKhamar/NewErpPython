"""Marketing: birthdays, bulk SMS, the contacts export, gift vouchers and paying an order with a voucher."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from newerp.api.deps import ClockDep, Session, enum_from_query
from newerp.api.security import Policies, require_roles
from newerp.api.sms import get_sender
from newerp.application.common import LookupItem, SystemClock
from newerp.application.marketing.campaigns import (
    BirthdayContactDto,
    BirthdaySmsRequest,
    BirthdaySmsResult,
    BulkSmsRequest,
    BulkSmsResult,
    ContactExportRequest,
    CouponCodeDto,
    CustomerOrderDto,
    MarketingOptions,
    MarketingService,
)
from newerp.application.marketing.vouchers import (
    OrderVoucherDto,
    RedeemVoucherRequest,
    SaveVoucherRequest,
    SendVoucherSmsRequest,
    SmsSendResultDto,
    VoucherCardDto,
    VoucherOptions,
    VoucherService,
)
from newerp.config import get_settings
from newerp.domain.marketing import BirthdayPeriod, ContactField, ContactSource
from newerp.infrastructure.marketing.campaigns import SqlMarketingStore
from newerp.infrastructure.marketing.vouchers import SqlVoucherStore
from newerp.infrastructure.sms import SqlSmsLog

orders_access = [Depends(require_roles(*Policies.ORDERS))]
router = APIRouter(prefix="/marketing", tags=["Маркетинг"], dependencies=orders_access)
# Separate endpoints, so the order requests and the composition change stay as they were. Access as to orders.
order_router = APIRouter(prefix="/orders/{order_id}/voucher", tags=["Маркетинг"], dependencies=orders_access)


def get_marketing(session: Session, clock: ClockDep) -> MarketingService:
    settings = get_settings()
    options = MarketingOptions(**settings.marketing.model_dump())
    store = SqlMarketingStore(session, settings.language_id)
    return MarketingService(store, get_sender(), SqlSmsLog(session), options, clock)


def get_vouchers(session: Session, clock: ClockDep) -> VoucherService:
    settings = get_settings()
    options = VoucherOptions(**settings.vouchers.model_dump())
    store = SqlVoucherStore(session, settings.language_id)
    return VoucherService(store, get_sender(), SqlSmsLog(session), options, clock)


Marketing = Annotated[MarketingService, Depends(get_marketing)]
Vouchers = Annotated[VoucherService, Depends(get_vouchers)]


# ═══ Birthdays and contacts ═══


@router.get(
    "/birthdays",
    summary="Іменинники періоду (Today, Tomorrow, Week, Month, All) з даними останнього замовлення й кількістю "
    "привітань цього року",
)
async def birthdays(service: Marketing, period: str = "Today") -> list[BirthdayContactDto]:
    return await service.birthdays(enum_from_query(BirthdayPeriod, period))


@router.post(
    "/birthdays/coupon-code", summary="Новий код купона DN…, якого ще немає (сам купон створюється разом із SMS)"
)
async def coupon_code(service: Marketing) -> CouponCodeDto:
    return await service.new_coupon_code()


@router.post(
    "/birthdays/sms",
    summary="Привітання одному клієнту; з кодом — спершу купон зі знижкою на birthday_coupon_days днів",
)
async def birthday_sms(request: BirthdaySmsRequest, service: Marketing) -> BirthdaySmsResult:
    return await service.send_birthday_sms(request)


@router.post("/birthdays/sms-bulk", summary="Однаковий текст багатьом номерам (до 500), без купонів")
async def bulk_sms(request: BulkSmsRequest, service: Marketing) -> BulkSmsResult:
    return await service.send_bulk(request)


@router.get("/customers/orders", summary="Замовлення клієнта за телефоном")
async def customer_orders(service: Marketing, phone: str) -> list[CustomerOrderDto]:
    return await service.customer_orders(phone)


@router.get(
    "/contacts.csv",
    summary="Унікальні телефони чи e-mail клієнтів (джерела: Orders, Questions, Subscribers) за період, CSV для Excel",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
async def contacts_csv(
    service: Marketing,
    field: str,
    sources: Annotated[list[str] | None, Query()] = None,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
) -> Response:
    combined = ContactSource.NONE
    for source in sources or []:
        combined |= enum_from_query(ContactSource, source)
    contact_field = enum_from_query(ContactField, field)
    csv = await service.export_csv(ContactExportRequest(field=contact_field, sources=combined, from_=from_, to=to))
    kind = "phones" if contact_field == ContactField.PHONE else "emails"
    name = f"contacts-{kind}-{SystemClock().now():%Y-%m-%d-%H%M}.csv"
    # Excel opens a UTF-8 CSV correctly only with the BOM at the start ("utf-8-sig" writes it).
    return Response(
        csv.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ═══ Gift vouchers ═══


@router.get("/vouchers", summary="Подарункові сертифікати з залишком (новіші вгорі, до 500); search — код або ім'я")
async def list_vouchers(service: Vouchers, search: str | None = None) -> list[VoucherCardDto]:
    return await service.list_cards(search)


@router.get("/vouchers/themes", summary="Теми (картинки) сертифікатів старого сайту")
async def voucher_themes(service: Vouchers) -> list[LookupItem]:
    return await service.themes()


@router.get("/vouchers/{voucher_id}", summary="Сертифікат з історією списань")
async def get_voucher(voucher_id: int, service: Vouchers) -> VoucherCardDto:
    return await service.get(voucher_id)


@router.post("/vouchers", summary="Новий сертифікат; порожній код згенерується (8 символів без 0/O й 1/I)")
async def create_voucher(request: SaveVoucherRequest, service: Vouchers) -> VoucherCardDto:
    return await service.create(request)


@router.put("/vouchers/{voucher_id}", summary="Змінити сертифікат (історія списань лишається)")
async def update_voucher(voucher_id: int, request: SaveVoucherRequest, service: Vouchers) -> VoucherCardDto:
    return await service.update(voucher_id, request)


@router.delete(
    "/vouchers/{voucher_id}", status_code=204, summary="Видалити сертифікат, яким ще не платили (інакше — вимкнути)"
)
async def delete_voucher(voucher_id: int, service: Vouchers) -> None:
    await service.delete(voucher_id)


@router.post(
    "/vouchers/{voucher_id}/sms",
    summary="Надіслати код і залишок отримувачу SMS (текст — vouchers.sms_text), із записом у журнал SMS",
)
async def voucher_sms(voucher_id: int, request: SendVoucherSmsRequest, service: Vouchers) -> SmsSendResultDto:
    return await service.send_sms(voucher_id, request.phone)


@order_router.get("", summary="Сертифікат, списаний у замовленні (204, якщо немає)")
async def order_voucher(order_id: int, service: Vouchers) -> OrderVoucherDto:
    spent = await service.for_order(order_id)
    return spent if spent is not None else Response(status_code=204)


@order_router.post(
    "",
    summary="Оплатити частину замовлення сертифікатом: рядок підсумку voucher, історія сертифіката, менша сума "
    "замовлення",
)
async def redeem_voucher(order_id: int, request: RedeemVoucherRequest, service: Vouchers) -> OrderVoucherDto:
    return await service.redeem(order_id, request.code, request.amount)


@order_router.delete(
    "",
    status_code=204,
    summary="Зняти сертифікат із замовлення: гроші повертаються на сертифікат, сума замовлення — як була",
)
async def release_voucher(order_id: int, service: Vouchers) -> None:
    await service.release(order_id)
