"""Order pages: the list with filters, the order card, a new order (OrderList, OrderCard, NewOrder.razor)."""

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import FormData

from newerp.api.deps import get_new_order_service, get_order_queries, get_order_service
from newerp.api.security import Policies
from newerp.application.orders.models import (
    AssignManagerRequest,
    ChangeOrderStatusRequest,
    CreateOrderRequest,
    CustomerRiskQuery,
    NewOrderLineRequest,
    OrderDetails,
    OrderFilter,
    OrderMethodsDto,
    OrderStatusDto,
    ProductPickDto,
)
from newerp.application.orders.services import NewOrderService, OrderService
from newerp.domain.common import DomainError
from newerp.infrastructure.orders.queries import SqlOrderQueries
from newerp.web.common import BUSINESS_ERRORS, WebUser, is_fragment, number, page_roles, render

router = APIRouter(prefix="/app/orders", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))])
Queries = Annotated[SqlOrderQueries, Depends(get_order_queries)]
Orders = Annotated[OrderService, Depends(get_order_service)]
NewOrders = Annotated[NewOrderService, Depends(get_new_order_service)]
Field = Annotated[str, Form()]

PAGE_SIZE = 20
# A status colour goes into a style attribute, so only a plain #rrggbb from the old site is used.
COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def _colors(statuses: list[OrderStatusDto]) -> dict[int, str]:
    return {status.id: status.color for status in statuses if COLOR.fullmatch(status.color)}


@router.get("")
async def order_list(
    request: Request,
    queries: Queries,
    status: str = "",
    manager: str = "",
    source: str = "",
    search: str = "",
    page: str = "1",
) -> HTMLResponse:
    current = max(number(page) or 1, 1)
    order_filter = OrderFilter(
        status_id=number(status),
        source_id=number(source),
        manager_id=number(manager),
        search=search.strip() or None,
        page=current,
        page_size=PAGE_SIZE,
    )
    result = await queries.list_orders(order_filter)
    pages = max(1, math.ceil(result.total_count / PAGE_SIZE))

    def link(to_page: int) -> str:
        fields = {"status": status, "manager": manager, "source": source, "search": search, "page": to_page}
        return urlencode({key: value for key, value in fields.items() if value})

    statuses = await queries.list_statuses()
    context: dict[str, Any] = {
        "result": result,
        "pages": pages,
        "colors": _colors(statuses),
        "previous_page": link(current - 1) if current > 1 else None,
        "next_page": link(current + 1) if current < pages else None,
    }
    if is_fragment(request):  # a filter changed: only the table is replaced
        return render(request, "orders/_table.html", **context)
    return render(
        request,
        "orders/list.html",
        order_filter=order_filter,
        statuses=statuses,
        managers=await queries.list_managers(),
        sources=await queries.list_sources(),
        **context,
    )


# ── New order (before /{order_id}, or "new" would be read as an order id) ──


@router.get("/new")
async def new_order_page(request: Request, queries: Queries, service: NewOrders) -> HTMLResponse:
    return render(
        request,
        "orders/new.html",
        methods=await service.list_methods(),
        statuses=await queries.list_statuses(),
        managers=await queries.list_managers(),
        sources=await queries.list_sources(),
    )


def _choices(product: ProductPickDto) -> list[tuple[str, str]]:
    """The buttons of a found product: one per size (or one without sizes), each with the query of its line."""
    line = {"product_id": product.id, "name": product.name, "model": product.model}
    if not product.options:
        return [("Додати", urlencode({**line, "price": product.price}))]
    return [
        (
            f"{option.value} ({option.quantity} шт.)",
            urlencode(
                {
                    **line,
                    "option_id": option.product_option_value_id,
                    "size": option.value,
                    "price": option.price if option.price > 0 else product.price,  # a size price replaces the base
                }
            ),
        )
        for option in product.options
    ]


@router.get("/new/products")
async def search_products(request: Request, service: NewOrders, search: str = "") -> HTMLResponse:
    products = await service.search_products(search) if search.strip() else []
    found = [(product, _choices(product)) for product in products]
    return render(request, "orders/_products.html", found=found, search=search.strip())


@router.get("/new/line")
async def new_line(
    request: Request,
    product_id: int,
    name: str = "",
    model: str = "",
    price: str = "0",
    option_id: int | None = None,
    size: str = "",
) -> HTMLResponse:
    """A row for the lines table; the server checks the product and the price only when the order is created."""
    line = {"product_id": product_id, "option_id": option_id, "name": name, "model": model, "size": size}
    return render(request, "orders/_line.html", price=price, **line)


@router.get("/new/risk")
async def customer_risk(
    request: Request,
    service: NewOrders,
    telephone: str = "",
    firstname: str = "",
    lastname: str = "",
    email: str = "",
) -> HTMLResponse:
    risk = await service.check_risk(CustomerRiskQuery(telephone, firstname, lastname, email))
    return render(request, "orders/_risk.html", risk=risk)


def _text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _amount(value: str, field: str) -> Decimal:
    try:
        amount = Decimal((value or "0").replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        amount = Decimal("NaN")
    if not amount.is_finite():
        raise DomainError(f"{field}: потрібне число, наприклад 150 або 99,50.")
    return amount


def _create_request(form: FormData, methods: OrderMethodsDto) -> CreateOrderRequest:
    """The form fields as the same request the API takes as JSON. The lines come as repeated fields."""
    columns = [form.getlist(name) for name in ("product_id", "option_id", "quantity", "price")]
    lines = [
        NewOrderLineRequest(
            product_id=number(str(product)) or 0,
            product_option_value_id=number(str(option)),
            quantity=number(str(quantity)) or 0,
            price=_amount(str(price), "Ціна"),
        )
        for product, option, quantity, price in zip(*columns, strict=True)
    ]
    shipping = {method.code: method.name for method in methods.shipping}
    payment = {method.code: method.name for method in methods.payment}
    return CreateOrderRequest(
        firstname=_text(form, "firstname"),
        lastname=_text(form, "lastname"),
        telephone=_text(form, "telephone"),
        email=_text(form, "email"),
        instagram=_text(form, "instagram"),
        order_source_id=number(_text(form, "order_source_id")) or 0,
        status_id=number(_text(form, "status_id")) or 0,
        manager_id=number(_text(form, "manager_id")),
        shipping_code=_text(form, "shipping_code"),
        shipping_method=shipping.get(_text(form, "shipping_code"), ""),
        city=_text(form, "city"),
        address1=_text(form, "address1"),
        address2=_text(form, "address2"),
        house=_text(form, "house"),
        flat=_text(form, "flat"),
        payment_code=_text(form, "payment_code"),
        payment_method=payment.get(_text(form, "payment_code"), ""),
        lines=lines,
        shipping_cost=_amount(_text(form, "shipping_cost"), "Доставка"),
        discount=_amount(_text(form, "discount"), "Знижка"),
        prepayment=_amount(_text(form, "prepayment"), "Передоплата"),
        comment=_text(form, "comment"),
    )


@router.post("/new")
async def create_order(request: Request, user: WebUser, service: NewOrders) -> HTMLResponse:
    form = await request.form()
    try:
        result = await service.create(_create_request(form, await service.list_methods()), user.user_id)
    except BUSINESS_ERRORS as error:
        return render(request, "orders/_error.html", error=str(error))  # under the form, which keeps its values
    response = render(request, "orders/_created.html", result=result)
    # On success the whole form is replaced, so the same order cannot be sent twice.
    response.headers["HX-Retarget"] = "#new-order"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


# ── Order card ──


async def _card(queries: SqlOrderQueries, order: OrderDetails) -> dict[str, Any]:
    statuses = await queries.list_statuses()
    return {
        "order": order,
        "statuses": statuses,
        "colors": _colors(statuses),
        "managers": await queries.list_managers(),
    }


@router.get("/{order_id}")
async def order_card(request: Request, order_id: int, queries: Queries) -> HTMLResponse:
    order = await queries.get(order_id)
    if order is None:
        return render(request, "not_found.html", status_code=404)
    return render(request, "orders/card.html", **await _card(queries, order))


@router.post("/{order_id}/status")
async def change_status(
    request: Request, order_id: int, user: WebUser, queries: Queries, service: Orders, status_id: Field = ""
) -> HTMLResponse:
    warnings: list[str] = []
    error = None
    try:
        change = ChangeOrderStatusRequest(status_id=number(status_id) or 0)
        warnings = (await service.change_status(order_id, change, user.user_id)).warnings
    except BUSINESS_ERRORS as exc:
        error = str(exc)
    order = await queries.get(order_id)
    if order is None:
        return render(request, "not_found.html", status_code=404)
    context = await _card(queries, order)
    return render(request, "orders/_status.html", warnings=warnings, error=error, saved=error is None, **context)


@router.post("/{order_id}/manager")
async def assign_manager(
    request: Request, order_id: int, user: WebUser, queries: Queries, service: Orders, manager_id: Field = ""
) -> HTMLResponse:
    error = None
    try:
        await service.assign_manager(order_id, AssignManagerRequest(manager_id=number(manager_id) or 0), user.user_id)
    except BUSINESS_ERRORS as exc:
        error = str(exc)
    order = await queries.get(order_id)
    if order is None:
        return render(request, "not_found.html", status_code=404)
    return render(request, "orders/_manager.html", error=error, saved=error is None, **await _card(queries, order))
