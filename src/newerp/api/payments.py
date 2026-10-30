"""Bank payments (PrivatBank statement) and LiqPay payment links. Access: Admin, Manager."""

from datetime import date
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from newerp.api.deps import ClockDep, Session, get_order_queries
from newerp.api.security import Policies, require_roles
from newerp.application.common import PagedResult
from newerp.application.payments import (
    BankImportResult,
    BankPaymentDto,
    BankPaymentFilter,
    BankPaymentService,
    CreatePaymentLinkRequest,
    LinkPaymentRequest,
    LiqPayService,
    LiqPayStatusDto,
    PaymentLinkDto,
    PaymentSuggestionDto,
)
from newerp.config import get_settings
from newerp.infrastructure.orders.queries import SqlOrderQueries
from newerp.infrastructure.payments import SqlBankPaymentRepository, bank_statement_client, liqpay_gateway

finance = [Depends(require_roles(*Policies.FINANCE))]
router = APIRouter(prefix="/payments/bank", tags=["Оплати"], dependencies=finance)
order_router = APIRouter(prefix="/orders/{order_id}", tags=["Оплати"], dependencies=finance)


@lru_cache
def get_bank_client():
    return bank_statement_client(get_settings().privat_bank)


@lru_cache
def get_liqpay():
    return liqpay_gateway(get_settings().liq_pay)


def get_bank_service(session: Session, clock: ClockDep) -> BankPaymentService:
    repository = SqlBankPaymentRepository(session, get_settings().language_id)
    return BankPaymentService(repository, get_bank_client(), clock)


def get_liqpay_service(orders: Annotated[SqlOrderQueries, Depends(get_order_queries)]) -> LiqPayService:
    return LiqPayService(orders, get_liqpay())


Bank = Annotated[BankPaymentService, Depends(get_bank_service)]
LiqPay = Annotated[LiqPayService, Depends(get_liqpay_service)]
From = Annotated[date | None, Query(alias="from")]


@router.get("", summary="Надходження з фільтрами (період, linked, замовлення, пошук, hidden), посторінково")
async def list_payments(
    service: Bank,
    from_: From = None,
    to: date | None = None,
    linked: bool | None = None,
    order_id: Annotated[int | None, Query(alias="orderId")] = None,
    search: str | None = None,
    hidden: bool = False,
    page: int = 1,
    page_size: Annotated[int, Query(alias="pageSize")] = 50,
) -> PagedResult[BankPaymentDto]:
    payment_filter = BankPaymentFilter(from_, to, linked, order_id, search, hidden, page, page_size)
    return await service.list_payments(payment_filter)


@router.post("/import", summary="Завантажити виписку (типово вчора й сьогодні); без ключів — clientConfigured = false")
async def import_statement(service: Bank, from_: From = None, to: date | None = None) -> BankImportResult:
    return await service.import_statement(from_, to)


@router.get("/{payment_id}/suggestions", summary="Замовлення, яким імовірно належить платіж, найкращі першими")
async def suggestions(payment_id: int, service: Bank, customer: str | None = None) -> list[PaymentSuggestionDto]:
    return await service.suggest(payment_id, customer)


@router.put("/{payment_id}/order", summary="Прив'язати платіж до замовлення (orderId = 0 — відв'язати)")
async def link(payment_id: int, request: LinkPaymentRequest, service: Bank) -> BankPaymentDto:
    return await service.link(payment_id, request)


@router.put("/{payment_id}/visible", status_code=204, summary="Приховати платіж (visible=false) або повернути його")
async def set_visible(payment_id: int, visible: bool, service: Bank) -> None:
    await service.set_visibility(payment_id, visible)


@order_router.get("/payments", summary="Банківські надходження, прив'язані до замовлення")
async def order_payments(order_id: int, service: Bank) -> list[BankPaymentDto]:
    return await service.for_order(order_id)


@order_router.post("/liqpay-link", summary="Посилання на оплату LiqPay; без ключів посилання тестове (isTest = true)")
async def liqpay_link(order_id: int, request: CreatePaymentLinkRequest, service: LiqPay) -> PaymentLinkDto:
    return await service.create_link(order_id, request)


@order_router.get("/liqpay-status", summary="Чи оплачено замовлення через LiqPay")
async def liqpay_status(order_id: int, service: LiqPay) -> LiqPayStatusDto:
    return await service.status(order_id)
