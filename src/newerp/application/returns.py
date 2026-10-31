"""Product returns (dd_return), like "Продажі → Повернення" of the old site, but tied to the order lines."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from newerp.application.common import Clock, Dto, NotFoundError, PagedResult, normalize_paging
from newerp.domain.common import DomainError
from newerp.domain.returns import MAX_COMMENT_LENGTH, ReturnableLine, validate_return


class ReturnFilter(Dto):
    return_id: int | None = None
    order_id: int | None = None
    status_id: int | None = None
    search: str | None = None  # customer, phone, product, model
    page: int = 1
    page_size: int = 20


class ReturnListItem(Dto):
    id: int
    order_id: int
    customer: str
    telephone: str
    product: str
    model: str
    quantity: int
    status_id: int
    status_name: str | None
    action_name: str | None
    date_added: datetime
    date_modified: datetime


class ReturnHistoryEntry(Dto):
    date_added: datetime
    status_id: int
    status_name: str | None
    comment: str


class ReturnDetails(Dto):
    id: int
    order_id: int
    product_id: int
    customer: str
    telephone: str
    email: str
    product: str
    model: str
    quantity: int
    opened: bool
    reason_id: int
    reason_name: str | None
    action_id: int
    action_name: str | None
    status_id: int
    status_name: str | None
    comment: str
    date_added: datetime
    date_modified: datetime
    history: list[ReturnHistoryEntry]


class ReturnLookupItem(Dto):
    id: int
    name: str


class ReturnLookupsDto(Dto):
    """Statuses, reasons and actions (money back, exchange…) of returns."""

    statuses: list[ReturnLookupItem]
    reasons: list[ReturnLookupItem]
    actions: list[ReturnLookupItem]


class CreateReturnRequest(Dto):
    """The customer and the product come from the order; action_id = 0: the action is not decided yet."""

    order_id: int
    product_id: int
    quantity: int
    reason_id: int
    status_id: int
    action_id: int = 0
    opened: bool = False
    comment: str | None = None


class AddReturnHistoryRequest(Dto):
    """A new status with a comment for the log and, when given, a new action."""

    status_id: int
    comment: str | None = None
    action_id: int | None = None


@dataclass(frozen=True)
class ReturnSourceOrder:
    """The order a return is made from: the customer, its lines and how much of each product is already returned."""

    order_id: int
    firstname: str
    lastname: str
    telephone: str
    email: str
    customer_id: int
    date_added: datetime
    lines: list[ReturnableLine]
    returned: dict[int, int]


@dataclass(frozen=True)
class NewReturnRecord:
    """What to write (all checks passed)."""

    order: ReturnSourceOrder
    line: ReturnableLine
    request: CreateReturnRequest
    now: datetime


class ReturnStore(Protocol):
    async def list_page(self, return_filter: ReturnFilter) -> PagedResult[ReturnListItem]: ...

    async def get(self, return_id: int) -> ReturnDetails | None: ...

    async def lookups(self) -> ReturnLookupsDto: ...

    async def get_source_order(self, order_id: int) -> ReturnSourceOrder | None: ...

    async def create(self, record: NewReturnRecord) -> int:
        """Writes the return and the first log entry; returns its id."""
        ...

    async def add_history(
        self, return_id: int, status_id: int, comment: str, action_id: int | None, now: datetime
    ) -> bool:
        """Like the old addReturnHistory: a new status (and action, when given) and a log entry."""
        ...


class ReturnService:
    def __init__(self, store: ReturnStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def list_page(self, return_filter: ReturnFilter) -> PagedResult[ReturnListItem]:
        page, page_size = normalize_paging(return_filter.page, return_filter.page_size)
        search = return_filter.search.strip() if return_filter.search else None
        return await self._store.list_page(
            return_filter.model_copy(update={"page": page, "page_size": page_size, "search": search})
        )

    async def get(self, return_id: int) -> ReturnDetails:
        details = await self._store.get(return_id)
        if details is None:
            raise NotFoundError(f"Повернення {return_id} не знайдено.")
        return details

    async def lookups(self) -> ReturnLookupsDto:
        return await self._store.lookups()

    async def create(self, request: CreateReturnRequest) -> int:
        order = await self._store.get_source_order(request.order_id)
        if order is None:
            raise DomainError(f"Замовлення {request.order_id} не знайдено.")
        lookups = await self._store.lookups()
        _require_known(lookups.statuses, request.status_id, "Оберіть статус повернення.")
        if request.reason_id > 0:
            _require_known(lookups.reasons, request.reason_id, "Такої причини повернення немає.")
        if request.action_id != 0:
            _require_known(lookups.actions, request.action_id, "Такої дії немає.")
        line = validate_return(
            order.firstname, order.lastname, order.telephone, order.email, order.lines, request.product_id,
            request.quantity, request.reason_id, request.comment, order.returned.get(request.product_id, 0),
        )  # fmt: skip
        return await self._store.create(NewReturnRecord(order, line, request, self._clock.now()))

    async def add_history(self, return_id: int, request: AddReturnHistoryRequest) -> None:
        lookups = await self._store.lookups()
        _require_known(lookups.statuses, request.status_id, "Оберіть статус повернення.")
        if request.action_id:
            _require_known(lookups.actions, request.action_id, "Такої дії немає.")
        comment = (request.comment or "").strip()
        if len(comment) > MAX_COMMENT_LENGTH:
            raise DomainError(f"Коментар довший за {MAX_COMMENT_LENGTH} символів.")
        if not await self._store.add_history(
            return_id, request.status_id, comment, request.action_id, self._clock.now()
        ):
            raise NotFoundError(f"Повернення {return_id} не знайдено.")


def _require_known(items: list[ReturnLookupItem], item_id: int, message: str) -> None:
    if not any(item.id == item_id for item in items):
        raise DomainError(message)
