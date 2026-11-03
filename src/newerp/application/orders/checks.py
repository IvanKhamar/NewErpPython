"""Shipping checks: before "Відправлено" / "Чекає на відправку", and the list of discrepancies (old admin's
checkShipping and getDiscrepancies)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from newerp.application.common import Clock, Dto, NotFoundError
from newerp.domain.orders import (
    ACCEPTED_STATUS_ID,
    AWAITING_SHIPMENT_STATUS_ID,
    DISCREPANCY_MAX_DAYS,
    NOVA_POSHTA_SHIPPING_CODES,
    SHIPPED_STATUS_ID,
    URGENT_STATUS_ID,
    WAYBILL_STALE_HOURS,
    IssueLevel,
    ShippingIssue,
    pickup_issues,
    shipped_issues,
)


@dataclass(frozen=True)
class OrderForCheck:
    customer: str
    telephone: str
    shipping_code: str
    shipping_method: str
    date_added: datetime


class ShippingCheckStore(Protocol):
    async def order_for_check(self, order_id: int) -> OrderForCheck | None: ...

    async def last_waybill(self, order_id: int) -> str:
        """The number (digits only) of the order's latest Nova Poshta waybill, or ""."""
        ...

    async def sms_phones_with(self, waybill: str, since: datetime) -> list[str]:
        """Phones the SMS log sent a text with this waybill number to (spaces in the text ignored)."""
        ...

    async def stale_waybills(
        self, status_ids: tuple[int, ...], shipping_codes: tuple[str, ...], created_before: datetime
    ) -> list[tuple[int, str, datetime]]:
        """(order id, customer, latest waybill time) of orders in these statuses with a waybill made before then."""
        ...

    async def orders_moved_into(
        self, status_id: int, since: datetime, shipping_codes: tuple[str, ...] | None
    ) -> list[int]:
        """Orders now in this status that entered it since then (by the status log)."""
        ...


class ShippingIssueDto(Dto):
    level: IssueLevel
    text: str


class ShippingCheckDto(Dto):
    """issues is empty when the order may get this status without a question."""

    order_id: int
    status_id: int
    customer: str
    telephone: str
    waybill: str
    issues: list[ShippingIssueDto]


class DiscrepancyDto(Dto):
    order_id: int
    customer: str
    level: IssueLevel
    text: str


class DiscrepancyGroupDto(Dto):
    title: str
    items: list[DiscrepancyDto]


class ShippingCheckService:
    def __init__(self, store: ShippingCheckStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def check(self, order_id: int, status_id: int) -> ShippingCheckDto:
        order = await self._store.order_for_check(order_id)
        if order is None:
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")
        waybill, issues = await self._issues(order_id, order, status_id)
        return ShippingCheckDto(
            order_id=order_id,
            status_id=status_id,
            customer=order.customer,
            telephone=order.telephone,
            waybill=waybill,
            issues=[ShippingIssueDto(level=i.level, text=i.text) for i in issues],
        )

    async def discrepancies(self, days: int) -> list[DiscrepancyGroupDto]:
        """Three groups, as on the old admin; days counts today, from 1 to 30."""
        days = max(1, min(DISCREPANCY_MAX_DAYS, days))
        now = self._clock.now()
        since = datetime.combine(now.date() - timedelta(days=days - 1), datetime.min.time())

        stale = await self._store.stale_waybills(
            (ACCEPTED_STATUS_ID, URGENT_STATUS_ID),
            NOVA_POSHTA_SHIPPING_CODES,
            now - timedelta(hours=WAYBILL_STALE_HOURS),
        )
        return [
            DiscrepancyGroupDto(
                title=f"ТТН створена понад {WAYBILL_STALE_HOURS} год тому, а статус досі «Прийнято» / «Терміново»",
                items=[
                    DiscrepancyDto(
                        order_id=order_id,
                        customer=customer,
                        level=IssueLevel.WARNING,
                        text=f"ТТН створена {created:%d.%m %H:%M}",
                    )
                    for order_id, customer, created in stale
                ],
            ),
            DiscrepancyGroupDto(
                title="«Відправлено», але є проблема з ТТН або SMS",
                items=await self._moved_with_issues(SHIPPED_STATUS_ID, since, NOVA_POSHTA_SHIPPING_CODES),
            ),
            DiscrepancyGroupDto(
                title="«Чекає на відправку», але схоже на доставку Новою Поштою",
                items=await self._moved_with_issues(AWAITING_SHIPMENT_STATUS_ID, since, None),
            ),
        ]

    async def _moved_with_issues(
        self, status_id: int, since: datetime, shipping_codes: tuple[str, ...] | None
    ) -> list[DiscrepancyDto]:
        items: list[DiscrepancyDto] = []
        for order_id in await self._store.orders_moved_into(status_id, since, shipping_codes):
            order = await self._store.order_for_check(order_id)
            if order is None:
                continue
            _, issues = await self._issues(order_id, order, status_id)
            items += [
                DiscrepancyDto(order_id=order_id, customer=order.customer, level=i.level, text=i.text) for i in issues
            ]
        return items

    async def _issues(self, order_id: int, order: OrderForCheck, status_id: int) -> tuple[str, list[ShippingIssue]]:
        waybill = await self._store.last_waybill(order_id)
        if status_id == SHIPPED_STATUS_ID:
            phones = await self._store.sms_phones_with(waybill, order.date_added) if waybill else []
            return waybill, shipped_issues(order.shipping_code, order.telephone, waybill, phones)
        if status_id == AWAITING_SHIPMENT_STATUS_ID:
            return waybill, pickup_issues(order.shipping_code, order.shipping_method, waybill)
        return waybill, []
