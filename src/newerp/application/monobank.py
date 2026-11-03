"""monobank payment links for orders (Python first, item 55): the old site's "plata by mono" module.

The storefront makes invoices when a customer pays by card, the manager makes one for an order by phone, and a
Celery beat task asks monobank about unfinished invoices of the last days, as the old admin's check did."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from newerp.application.common import Clock, Dto, Money, NotFoundError
from newerp.application.orders.ports import OrderQueries
from newerp.application.payments import CreatePaymentLinkRequest
from newerp.domain.payments import MONO_LABELS, MonoState, mono_state, to_kopecks, validate_liqpay_amount

audit = logging.getLogger("newerp.audit")


@dataclass(frozen=True)
class MonoInvoice:
    """What monobank says about an invoice; amounts in kopecks."""

    invoice_id: str
    status: str
    amount: int
    created: datetime | None
    modified: datetime | None


@dataclass(frozen=True)
class CreatedMonoInvoice:
    invoice_id: str
    page_url: str


class MonoInvoiceDto(Dto):
    invoice_id: str
    order_id: int
    status: str
    label: str
    paid: bool
    amount: Money
    created: datetime | None
    modified: datetime | None


class MonoLinkDto(Dto):
    url: str
    invoice_id: str
    amount: Money
    is_test: bool


class MonoCheckResult(Dto):
    client_configured: bool
    checked: int
    changed: int
    removed_duplicates: int


class MonobankGateway(Protocol):
    @property
    def is_test(self) -> bool:
        """No token: test invoices that cannot be paid."""
        ...

    async def create_invoice(
        self, amount_kopecks: int, reference: str, destination: str, redirect_url: str
    ) -> CreatedMonoInvoice: ...

    async def invoice(self, invoice_id: str) -> MonoInvoice: ...


class MonoInvoiceStore(Protocol):
    async def for_order(self, order_id: int) -> list[MonoInvoiceDto]: ...

    async def add(self, order_id: int, invoice_id: str, amount: Decimal, now: datetime) -> None: ...

    async def unfinished_since(self, since: datetime) -> list[tuple[str, str]]:
        """(invoice id, saved status) of invoices not yet in a final state, made since then."""
        ...

    async def update(self, invoice: MonoInvoice) -> None: ...

    async def remove_unpaid_duplicates(self, before: datetime) -> int:
        """Older than then, unpaid invoices of orders that have a paid one: the customer paid another link."""
        ...


class MonobankService:
    def __init__(
        self, orders: OrderQueries, store: MonoInvoiceStore, gateway: MonobankGateway, clock: Clock,
        redirect_url: str, check_days: int,
    ) -> None:  # fmt: skip
        self._orders = orders
        self._store = store
        self._gateway = gateway
        self._clock = clock
        self._redirect_url = redirect_url
        self._check_days = check_days

    async def for_order(self, order_id: int) -> list[MonoInvoiceDto]:
        return await self._store.for_order(order_id)

    async def create_link(self, order_id: int, request: CreatePaymentLinkRequest, user_id: int) -> MonoLinkDto:
        order = await self._orders.get(order_id)
        if order is None:
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")
        amount = validate_liqpay_amount(request.amount, order.total)  # the same rule: the order or a part of it
        created = await self._gateway.create_invoice(
            to_kopecks(amount), str(order_id), f"Оплата замовлення #{order_id}", self._redirect_url
        )
        await self._store.add(order_id, created.invoice_id, amount, self._clock.now().replace(microsecond=0))
        audit.info("monobank invoice made: order %s, %s UAH, by user %s", order_id, amount, user_id)
        return MonoLinkDto(
            url=created.page_url, invoice_id=created.invoice_id, amount=amount, is_test=self._gateway.is_test
        )

    async def check(self) -> MonoCheckResult:
        """Asks monobank about every unfinished invoice of the last days and saves what changed."""
        if self._gateway.is_test:
            return MonoCheckResult(client_configured=False, checked=0, changed=0, removed_duplicates=0)
        now = self._clock.now()
        removed = await self._store.remove_unpaid_duplicates(now - timedelta(days=self._check_days))
        unfinished = await self._store.unfinished_since(now - timedelta(days=self._check_days))
        changed = 0
        for invoice_id, saved_status in unfinished:
            invoice = await self._gateway.invoice(invoice_id)
            if invoice.status != saved_status:
                await self._store.update(invoice)
                changed += 1
        return MonoCheckResult(
            client_configured=True, checked=len(unfinished), changed=changed, removed_duplicates=removed
        )


def invoice_dto(order_id: int, invoice_id: str, status: str, amount: Decimal, created, modified) -> MonoInvoiceDto:
    state = mono_state(status)
    return MonoInvoiceDto(
        invoice_id=invoice_id, order_id=order_id, status=status or "",
        label=MONO_LABELS[state] if state else "Очікує оновлення інформації",
        paid=state == MonoState.SUCCESS, amount=amount, created=created, modified=modified,
    )  # fmt: skip
