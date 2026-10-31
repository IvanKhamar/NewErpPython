"""Registered customers (dd_customer): totals by their orders and managers' notes."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from newerp.domain.common import DomainError
from newerp.domain.orders import CANCELLED_STATUS_ID

NOTE_MAX_LENGTH = 2000


@dataclass(frozen=True)
class CustomerOrder:
    """A customer's order, briefly: for the totals in the card."""

    order_id: int
    date_added: datetime
    status_id: int
    total: Decimal


@dataclass(frozen=True)
class CustomerSummary:
    """The old site's customer card showed no orders at all; the manager searched the order list instead."""

    order_count: int
    cancelled_count: int
    spent: Decimal
    first_order: datetime | None
    last_order: datetime | None

    @staticmethod
    def of(orders: Sequence[CustomerOrder]) -> "CustomerSummary":
        """Cancelled orders ("Відмінено") are not in the spent sum, but they are counted apart."""
        dates = [o.date_added for o in orders]
        return CustomerSummary(
            order_count=len(orders),
            cancelled_count=sum(1 for o in orders if o.status_id == CANCELLED_STATUS_ID),
            spent=sum((o.total for o in orders if o.status_id != CANCELLED_STATUS_ID), Decimal(0)),
            first_order=min(dates, default=None),
            last_order=max(dates, default=None),
        )


def validate_note(comment: str | None) -> str:
    """A manager's note about a customer (dd_customer_history, the "Історія" tab of the old customer card)."""
    text = (comment or "").strip()
    if not text:
        raise DomainError("Напишіть текст нотатки.")
    if len(text) > NOTE_MAX_LENGTH:
        raise DomainError(f"Нотатка довша за {NOTE_MAX_LENGTH} символів.")
    return text
