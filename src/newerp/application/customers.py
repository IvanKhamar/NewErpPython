"""Customers (dd_customer), like "Покупці → Покупці" of the old site, with their orders and managers' notes."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from newerp.application.common import Clock, Dto, Money, NotFoundError, PagedResult, normalize_paging
from newerp.domain.customers import CustomerOrder, CustomerSummary, validate_note
from newerp.domain.orders import CustomerRisk


class CustomerFilter(Dto):
    search: str | None = None  # name, e-mail, phone, id
    group_id: int | None = None
    status: bool | None = None
    page: int = 1
    page_size: int = 20


class CustomerListItem(Dto):
    id: int
    name: str
    email: str
    telephone: str
    group_name: str | None
    status: bool
    date_added: datetime
    order_count: int


class CustomerGroupDto(Dto):
    id: int
    name: str


class CustomerAddressDto(Dto):
    id: int
    name: str
    city: str
    address1: str
    address2: str
    is_default: bool


class CustomerOrderItem(Dto):
    """by_phone: found by the same phone, not by customer_id (a guest order)."""

    id: int
    date_added: datetime
    status_id: int
    status_name: str | None
    total: Money
    currency_code: str
    by_phone: bool


class CustomerNoteDto(Dto):
    date_added: datetime
    comment: str


class CustomerStatsDto(Dto):
    """Totals of the orders; risk is the same rating as in the new-order form, by name (Red) like .NET ToString()."""

    order_count: int
    cancelled_count: int
    spent: Money
    first_order: datetime | None
    last_order: datetime | None
    risk_level: str
    risk_message: str


class CustomerDetails(Dto):
    id: int
    firstname: str
    lastname: str
    email: str
    telephone: str
    group_name: str | None
    status: bool
    newsletter: bool
    date_added: datetime
    addresses: list[CustomerAddressDto]
    orders: list[CustomerOrderItem]
    stats: CustomerStatsDto
    notes: list[CustomerNoteDto]


class AddCustomerNoteRequest(Dto):
    comment: str | None = None


@dataclass(frozen=True)
class CustomerProfile:
    """A customer without orders: what the store gives the service."""

    id: int
    firstname: str
    lastname: str
    email: str
    telephone: str
    group_name: str | None
    status: bool
    newsletter: bool
    date_added: datetime
    addresses: list[CustomerAddressDto]
    notes: list[CustomerNoteDto]


class CustomerStore(Protocol):
    async def list_page(self, customer_filter: CustomerFilter) -> PagedResult[CustomerListItem]: ...

    async def groups(self) -> list[CustomerGroupDto]: ...

    async def get(self, customer_id: int) -> CustomerProfile | None: ...

    async def orders(self, customer_id: int, phone_key: str | None) -> list[CustomerOrderItem]:
        """By customer_id and by the same phone (phone_key: the last 9 digits), as guests have no customer_id."""
        ...

    async def add_note(self, customer_id: int, comment: str, now: datetime) -> bool:
        """False when there is no such customer."""
        ...


class CustomerService:
    def __init__(self, store: CustomerStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def list_page(self, customer_filter: CustomerFilter) -> PagedResult[CustomerListItem]:
        page, page_size = normalize_paging(customer_filter.page, customer_filter.page_size)
        search = customer_filter.search.strip() if customer_filter.search else None
        return await self._store.list_page(
            customer_filter.model_copy(update={"page": page, "page_size": page_size, "search": search})
        )

    async def groups(self) -> list[CustomerGroupDto]:
        return await self._store.groups()

    async def get(self, customer_id: int) -> CustomerDetails:
        c = await self._store.get(customer_id)
        if c is None:
            raise NotFoundError(f"Покупця {customer_id} не знайдено.")
        orders = await self._store.orders(customer_id, CustomerRisk.phone_key(c.telephone))
        summary = CustomerSummary.of([CustomerOrder(o.id, o.date_added, o.status_id, o.total) for o in orders])
        risk = CustomerRisk.of([(o.id, o.status_id) for o in orders])
        stats = CustomerStatsDto(
            order_count=summary.order_count,
            cancelled_count=summary.cancelled_count,
            spent=summary.spent,
            first_order=summary.first_order,
            last_order=summary.last_order,
            risk_level=risk.level.name.title(),
            risk_message=risk.message,
        )
        return CustomerDetails(
            id=c.id, firstname=c.firstname, lastname=c.lastname, email=c.email, telephone=c.telephone,
            group_name=c.group_name, status=c.status, newsletter=c.newsletter, date_added=c.date_added,
            addresses=c.addresses, orders=orders, stats=stats, notes=c.notes,
        )  # fmt: skip

    async def add_note(self, customer_id: int, request: AddCustomerNoteRequest) -> None:
        comment = validate_note(request.comment)
        if not await self._store.add_note(customer_id, comment, self._clock.now()):
            raise NotFoundError(f"Покупця {customer_id} не знайдено.")
