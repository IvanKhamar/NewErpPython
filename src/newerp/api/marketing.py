"""Marketing: birthdays, bulk SMS, the contacts export, gift vouchers and paying an order with a voucher."""

from datetime import date
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from newerp.api.deps import ClockDep, Session, enum_from_query
from newerp.api.security import CurrentPrincipal, Policies, require_roles
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
from newerp.application.marketing.coupons import CouponCardDto, CouponListItem, CouponService, SaveCouponRequest
from newerp.application.marketing.tracking import TrackingCodeDto, TrackingService
from newerp.application.marketing.voucher_admin import VoucherAdminService, VoucherMailResult, VoucherThemeDto
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
from newerp.infrastructure.mail import mail_sender
from newerp.infrastructure.marketing.campaigns import SqlMarketingStore
from newerp.infrastructure.marketing.coupons import SqlCouponStore
from newerp.infrastructure.marketing.tracking import SqlTrackingStore, SqlVoucherThemeStore
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


# ═══ Coupons (Python first: the .NET and Java versions have only birthday coupons) ═══


def get_coupons(session: Session, clock: ClockDep) -> CouponService:
    return CouponService(SqlCouponStore(session, get_settings().language_id), clock)


Coupons = Annotated[CouponService, Depends(get_coupons)]


@router.get("/coupons", summary="Купони (новіші вгорі, до 500) з кількістю використань; search — код або назва")
async def list_coupons(service: Coupons, search: str | None = None) -> list[CouponListItem]:
    return await service.list_coupons(search)


@router.get("/coupons/{coupon_id}", summary="Купон з товарами й категоріями, на які діє і на які ні, та історією")
async def get_coupon(coupon_id: int, service: Coupons) -> CouponCardDto:
    return await service.get(coupon_id)


@router.post(
    "/coupons",
    summary="Новий купон: type P (відсоток) або F (сума), дати обов'язкові; списки товарів і категорій — на що діє "
    "(порожньо — на весь кошик) і виключення",
)
async def create_coupon(request: SaveCouponRequest, service: Coupons) -> CouponCardDto:
    return await service.create(request)


@router.put("/coupons/{coupon_id}", summary="Змінити купон разом зі списками")
async def update_coupon(coupon_id: int, request: SaveCouponRequest, service: Coupons) -> CouponCardDto:
    return await service.update(coupon_id, request)


@router.delete(
    "/coupons/{coupon_id}", status_code=204, summary="Видалити купон, яким ще не користувались (інакше — вимкнути)"
)
async def delete_coupon(coupon_id: int, service: Coupons) -> None:
    await service.delete(coupon_id)


# ═══ Tracking codes, voucher e-mail and themes (Python first) ═══


@lru_cache
def get_mail():
    return mail_sender(get_settings().mail)


def get_tracking(session: Session, clock: ClockDep) -> TrackingService:
    return TrackingService(SqlTrackingStore(session), clock, get_settings().customers.store_url)


def get_voucher_admin(session: Session, vouchers: Vouchers) -> VoucherAdminService:
    return VoucherAdminService(vouchers, SqlVoucherThemeStore(session, get_settings().language_id), get_mail())


Tracking = Annotated[TrackingService, Depends(get_tracking)]
VoucherAdmin = Annotated[VoucherAdminService, Depends(get_voucher_admin)]


@router.get("/tracking", summary="Коди відстеження кампаній: кліки, замовлення, сума")
async def list_tracking(service: Tracking) -> list[TrackingCodeDto]:
    return await service.codes()


@router.post("/tracking", status_code=201, summary="Новий код (латиниця, цифри, - _), унікальний")
async def create_tracking(request: TrackingCodeDto, service: Tracking) -> TrackingCodeDto:
    return await service.save(None, request)


@router.put("/tracking/{marketing_id}", summary="Змінити код відстеження")
async def update_tracking(marketing_id: int, request: TrackingCodeDto, service: Tracking) -> TrackingCodeDto:
    return await service.save(marketing_id, request)


@router.delete("/tracking/{marketing_id}", status_code=204, summary="Видалити код (замовлення його зберігають)")
async def delete_tracking(marketing_id: int, principal: CurrentPrincipal, service: Tracking) -> None:
    await service.delete(marketing_id, principal.user_id)


@router.post("/vouchers/{voucher_id}/email", summary="Надіслати сертифікат листом (на to_email або вказану адресу)")
async def email_voucher(
    voucher_id: int, principal: CurrentPrincipal, service: VoucherAdmin, to: str | None = None
) -> VoucherMailResult:
    return await service.send_email(voucher_id, to, principal.user_id)


@router.get("/vouchers/themes/details", summary="Теми сертифікатів з картинками й кількістю сертифікатів")
async def list_voucher_themes(service: VoucherAdmin) -> list[VoucherThemeDto]:
    return await service.themes()


@router.post("/vouchers/themes", status_code=201, summary="Нова тема сертифіката")
async def create_voucher_theme(request: VoucherThemeDto, service: VoucherAdmin) -> VoucherThemeDto:
    return await service.save_theme(None, request)


@router.put("/vouchers/themes/{theme_id}", summary="Змінити тему сертифіката")
async def update_voucher_theme(theme_id: int, request: VoucherThemeDto, service: VoucherAdmin) -> VoucherThemeDto:
    return await service.save_theme(theme_id, request)


@router.delete("/vouchers/themes/{theme_id}", status_code=204, summary="Видалити тему, якої не мають сертифікати")
async def delete_voucher_theme(theme_id: int, principal: CurrentPrincipal, service: VoucherAdmin) -> None:
    await service.delete_theme(theme_id, principal.user_id)
