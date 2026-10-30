from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

from newerp.application.common import Clock, NotFoundError
from newerp.application.orders.models import (
    AssignManagerRequest,
    ChangeOrderStatusRequest,
    ChangeOrderStatusResult,
    CreateOrderRequest,
    CreateOrderResult,
    CustomerPickDto,
    CustomerRiskDto,
    CustomerRiskQuery,
    NewOrderLineRecord,
    NewOrderLineRequest,
    NewOrderRecord,
    OrderCompositionDto,
    OrderMethodsDto,
    ProductOptionValueDto,
    ProductPickDto,
    UpdateOrderCompositionRequest,
    UpdateOrderCompositionResult,
)
from newerp.application.orders.ports import NewOrderStore, OrderStatusHandler, OrderStore
from newerp.domain.common import DomainError, plain
from newerp.domain.orders import RISK_LOOKBACK_YEARS, CustomerRisk, OrderDraft, OrderDraftLine, OrderStatusChange


class OrderService:
    """Changing an order: its status and responsible manager."""

    def __init__(self, store: OrderStore, clock: Clock, handlers: Sequence[OrderStatusHandler] = ()) -> None:
        self._store = store
        self._clock = clock
        self._handlers = handlers

    async def change_status(
        self, order_id: int, request: ChangeOrderStatusRequest, user_id: int
    ) -> ChangeOrderStatusResult:
        current_status_id = await self._store.get_status_id(order_id)
        if current_status_id is None:
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")
        if not await self._store.status_exists(request.status_id):
            raise DomainError(f"Статусу {request.status_id} не існує.")
        await self._require_active_user(user_id)

        change = OrderStatusChange.create(order_id, current_status_id, request.status_id, user_id, self._clock.now())
        # Handlers (for example, writing off consumables) run before the status is saved: if one fails, the status
        # stays old and the action can simply be repeated. They skip a repeated write-off themselves.
        warnings: list[str] = []
        for handler in self._handlers:
            warnings += await handler.on_status_changing(change)
        await self._store.save_status_change(change)
        return ChangeOrderStatusResult(warnings=warnings)

    async def reapply_status(
        self, order_id: int, status_id: int, change: Callable[[], Awaitable[None]], user_id: int
    ) -> list[str]:
        """
        Changes an order's composition so other modules (sewn stock) take it into account. Like editOrder of the old
        site: first the order leaves its status (handlers give back what it held), then the lines change, and the
        order enters the status again (handlers take by the new lines). Status and status log stay unchanged.
        """
        if status_id <= 0:
            await change()
            return []

        now = self._clock.now()
        for handler in self._handlers:
            await handler.on_status_changing(OrderStatusChange(order_id, status_id, 0, user_id, now))
        await change()
        warnings: list[str] = []
        for handler in self._handlers:
            warnings += await handler.on_status_changing(OrderStatusChange(order_id, 0, status_id, user_id, now))
        return warnings

    async def assign_manager(self, order_id: int, request: AssignManagerRequest, user_id: int) -> None:
        if await self._store.get_status_id(order_id) is None:
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")
        await self._require_active_user(request.manager_id)
        await self._require_active_user(user_id)
        await self._store.save_manager(order_id, request.manager_id, user_id, self._clock.now())

    async def _require_active_user(self, user_id: int) -> None:
        if not await self._store.is_active_user(user_id):
            raise DomainError(f"Користувача {user_id} не знайдено або його вимкнено.")


class NewOrderService:
    """
    Quick manual orders (by phone or from social networks), like the old site's "Quick order" form, and the check
    whether the customer cancelled earlier orders.
    """

    SEARCH_LIMIT = 10

    def __init__(self, store: NewOrderStore, orders: OrderStore, order_service: OrderService, clock: Clock) -> None:
        self._store = store
        self._orders = orders
        self._order_service = order_service
        self._clock = clock

    async def search_products(self, term: str | None) -> list[ProductPickDto]:
        if not term or not term.strip():
            return []
        return await self._store.search_products(term.strip(), self.SEARCH_LIMIT)

    async def search_customers(self, term: str | None) -> list[CustomerPickDto]:
        if not term or len(term.strip()) < 3:
            return []
        return await self._store.search_customers(term.strip(), self.SEARCH_LIMIT)

    async def list_methods(self) -> OrderMethodsDto:
        return await self._store.list_methods()

    async def check_risk(self, query: CustomerRiskQuery) -> CustomerRiskDto:
        phone_key = CustomerRisk.phone_key(query.telephone)
        firstname = (query.firstname or "").strip()
        lastname = (query.lastname or "").strip()
        has_name = bool(firstname and lastname)
        email = (query.email or "").strip()
        if phone_key is None and not has_name and not email:
            return _risk_dto(CustomerRisk.of([]))

        now = self._clock.now()
        since = _years_back(now, RISK_LOOKBACK_YEARS)
        found = await self._store.find_customer_orders(
            phone_key, firstname if has_name else None, lastname if has_name else None, email or None, since
        )
        return _risk_dto(CustomerRisk.of(found))

    async def create(self, request: CreateOrderRequest, user_id: int) -> CreateOrderResult:
        lines = _draft_lines(request.lines)
        draft = OrderDraft.create(
            request.firstname, request.telephone, request.shipping_method, request.payment_method,
            lines, request.shipping_cost, request.discount, request.prepayment,
        )  # fmt: skip
        if not await self._orders.status_exists(request.status_id):
            raise DomainError(f"Статусу {request.status_id} не існує.")
        if request.order_source_id != 0 and not await self._store.source_exists(request.order_source_id):
            raise DomainError(f"Джерела замовлення {request.order_source_id} не існує.")
        manager_id = request.manager_id if request.manager_id is not None else user_id
        if not await self._orders.is_active_user(manager_id):
            raise DomainError(f"Користувача {manager_id} не знайдено або його вимкнено.")

        records = await self._records(lines)
        # Risk is counted before saving, so the new order does not count itself.
        risk = await self.check_risk(
            CustomerRiskQuery(request.telephone, request.firstname, request.lastname, request.email)
        )
        # As on the old site: first the order without a status, then the status through the usual status change,
        # so the log and the other modules' handlers (writing off sewn pieces, etc.) run.
        now = self._clock.now()
        order_id = await self._store.create(NewOrderRecord(request, draft, records, now))
        await self._orders.save_manager(order_id, manager_id, user_id, now)
        status = await self._order_service.change_status(
            order_id, ChangeOrderStatusRequest(status_id=request.status_id), user_id
        )
        return CreateOrderResult(order_id=order_id, warnings=status.warnings, risk=risk)

    async def get_composition(self, order_id: int) -> OrderCompositionDto:
        composition = await self._store.get_composition(order_id)
        if composition is None:
            raise NotFoundError(f"Замовлення {order_id} не знайдено.")
        return composition

    async def update_composition(
        self, order_id: int, request: UpdateOrderCompositionRequest, user_id: int
    ) -> UpdateOrderCompositionResult:
        """New products, sizes, quantities and sums; sewn pieces are recounted if the status writes them off."""
        current = await self.get_composition(order_id)
        lines = _draft_lines(request.lines)
        draft = OrderDraft.for_lines(lines, request.shipping_cost, request.discount, request.prepayment)
        if not await self._orders.is_active_user(user_id):
            raise DomainError(f"Користувача {user_id} не знайдено або його вимкнено.")
        records = await self._records(lines)

        # A gift voucher already used in the order stays: the new sum must cover it and the advance.
        voucher = await self._store.voucher_paid(order_id)
        total = draft.total - voucher
        if total < 0:
            raise DomainError(
                f"Сума замовлення стала меншою за оплату сертифікатом ({plain(voucher)} грн). "
                "Спершу зніміть сертифікат."
            )
        if draft.prepayment > total:
            raise DomainError("Сума завдатку не може перевищувати суму замовлення.")

        async def replace() -> None:
            await self._store.replace_lines(order_id, draft, records, self._clock.now())

        warnings = await self._order_service.reapply_status(order_id, current.status_id, replace, user_id)
        return UpdateOrderCompositionResult(total=total, warnings=warnings)

    async def _records(self, lines: list[OrderDraftLine]) -> list[NewOrderLineRecord]:
        product_ids = list(dict.fromkeys(line.product_id for line in lines))
        products = {p.id: p for p in await self._store.get_products(product_ids)}
        return [_record(line, products) for line in lines]


def _draft_lines(lines: list[NewOrderLineRequest] | None) -> list[OrderDraftLine]:
    return [
        OrderDraftLine(line.product_id, line.product_option_value_id, line.quantity, line.price) for line in lines or []
    ]


def _record(line: OrderDraftLine, products: dict[int, ProductPickDto]) -> NewOrderLineRecord:
    product = products.get(line.product_id)
    if product is None:
        raise DomainError(f"Товару {line.product_id} не знайдено.")
    option: ProductOptionValueDto | None = None
    if line.product_option_value_id is not None:
        option = next((o for o in product.options if o.product_option_value_id == line.product_option_value_id), None)
        if option is None:
            raise DomainError(
                f"Товар «{product.name}» не має такого розміру чи опції ({line.product_option_value_id})."
            )
    elif product.options:
        raise DomainError(f"Оберіть розмір чи опцію для товару «{product.name}».")
    return NewOrderLineRecord(line, product.name, product.model, option)


def _risk_dto(risk: CustomerRisk) -> CustomerRiskDto:
    return CustomerRiskDto(
        level=risk.level,
        total_orders=risk.total_orders,
        cancelled_orders=len(risk.cancelled_order_ids),
        cancelled_order_ids=risk.cancelled_order_ids,
        suspicion_rate=risk.suspicion_rate,
        message=risk.message,
    )


def _years_back(moment: datetime, years: int) -> datetime:
    """Like DateTime.AddYears(-n): 29 February becomes 28 February in a common year."""
    try:
        return moment.replace(year=moment.year - years)
    except ValueError:
        return moment.replace(year=moment.year - years, day=28)
