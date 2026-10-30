from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from pydantic import Field

from newerp.application.common import Dto, Money
from newerp.domain.orders import CustomerRiskLevel, OrderDraft, OrderDraftLine


@dataclass(frozen=True)
class OrderFilter:
    status_id: int | None = None
    source_id: int | None = None
    manager_id: int | None = None
    from_: date | None = None
    to: date | None = None
    search: str | None = None
    page: int = 1
    page_size: int = 20


class OrderListItem(Dto):
    id: int
    date_added: datetime
    customer: str
    telephone: str
    total: Money
    currency_code: str
    status_id: int
    status_name: str | None
    source_name: str | None
    manager_id: int | None
    manager_name: str | None


class OrderProductLine(Dto):
    product_id: int
    name: str
    model: str
    quantity: int
    price: Money
    total: Money


class OrderStatusHistoryEntry(Dto):
    changed_at: datetime
    old_status_id: int
    new_status_id: int
    new_status_name: str | None
    user_id: int
    user_name: str | None


class OrderDetails(Dto):
    id: int
    date_added: datetime
    customer: str
    telephone: str
    email: str
    shipping_method: str
    shipping_city: str
    shipping_address: str
    payment_method: str
    comment: str
    total: Money
    currency_code: str
    status_id: int
    status_name: str | None
    source_id: int
    source_name: str | None
    manager_id: int | None
    manager_name: str | None
    products: list[OrderProductLine]
    status_history: list[OrderStatusHistoryEntry]


class ChangeOrderStatusRequest(Dto):
    """Who changes the status comes from the session, not from the body, so it cannot be faked."""

    status_id: int = 0


class ChangeOrderStatusResult(Dto):
    """Warnings from other modules, for example a shortage of consumables when writing them off."""

    warnings: list[str]


class AssignManagerRequest(Dto):
    manager_id: int = 0


class OrderStatusDto(Dto):
    """A status from the lookup. color: the old site's colour (#rrggbb) or empty."""

    id: int
    name: str
    color: str


# ── Manual orders ──


class NewOrderLineRequest(Dto):
    product_id: int = 0
    product_option_value_id: int | None = None
    quantity: int = 0
    price: Decimal = Decimal(0)


class CreateOrderRequest(Dto):
    """An order a manager creates by hand. No manager_id: the creator becomes responsible."""

    firstname: str | None = None
    lastname: str | None = None
    telephone: str | None = None
    email: str | None = None
    instagram: str | None = None
    order_source_id: int = 0
    status_id: int = 0
    manager_id: int | None = None
    shipping_code: str | None = None
    shipping_method: str | None = None
    city: str | None = None
    address1: str | None = None
    address2: str | None = None
    house: str | None = None
    flat: str | None = None
    payment_code: str | None = None
    payment_method: str | None = None
    lines: list[NewOrderLineRequest] | None = None
    shipping_cost: Decimal = Decimal(0)
    discount: Decimal = Decimal(0)
    prepayment: Decimal = Decimal(0)
    comment: str | None = ""


class CustomerRiskDto(Dto):
    level: CustomerRiskLevel
    total_orders: int
    cancelled_orders: int
    cancelled_order_ids: list[int]
    suspicion_rate: Money
    message: str


class CreateOrderResult(Dto):
    """warnings: from other modules after the status is set (for example, a shortage of sewn pieces)."""

    order_id: int
    warnings: list[str]
    risk: CustomerRiskDto


class ProductOptionValueDto(Dto):
    product_option_id: int
    product_option_value_id: int
    option_name: str
    value: str
    quantity: int
    price: Money


class ProductPickDto(Dto):
    """A product to add to an order. price is the base price; an option price above 0 replaces it."""

    id: int
    name: str
    model: str
    price: Money
    quantity: int
    options: list[ProductOptionValueDto]


class CustomerPickDto(Dto):
    """Customer contacts from their latest order."""

    customer_id: int
    firstname: str
    lastname: str
    telephone: str
    email: str
    instagram: str
    city: str
    address1: str
    address2: str
    last_order_id: int


@dataclass(frozen=True)
class CustomerRiskQuery:
    telephone: str | None = None
    firstname: str | None = None
    lastname: str | None = None
    email: str | None = None


class OrderMethodDto(Dto):
    """A shipping or payment method: code (as in OpenCart) and name."""

    code: str
    name: str


class OrderMethodsDto(Dto):
    shipping: list[OrderMethodDto]
    payment: list[OrderMethodDto]


@dataclass(frozen=True)
class NewOrderLineRecord:
    line: OrderDraftLine
    name: str
    model: str
    option: ProductOptionValueDto | None


@dataclass(frozen=True)
class NewOrderRecord:
    """What to write for a new order (all checks passed)."""

    request: CreateOrderRequest
    draft: OrderDraft
    lines: list[NewOrderLineRecord]
    created_at: datetime


class OrderCompositionLine(Dto):
    """product_option_value_id: the chosen size (None without options); options: the product's sizes now."""

    product_id: int
    product_option_value_id: int | None
    name: str
    model: str
    option: str
    quantity: int
    price: Money
    options: list[ProductOptionValueDto]


class OrderCompositionDto(Dto):
    order_id: int
    status_id: int
    lines: list[OrderCompositionLine]
    shipping_cost: Money
    discount: Money
    prepayment: Money
    total: Money


class UpdateOrderCompositionRequest(Dto):
    """The new composition fully replaces the old one (like editing an order on the old site)."""

    lines: list[NewOrderLineRequest] | None = Field(default=None)
    shipping_cost: Decimal = Decimal(0)
    discount: Decimal = Decimal(0)
    prepayment: Decimal = Decimal(0)


class UpdateOrderCompositionResult(Dto):
    total: Money
    warnings: list[str]
