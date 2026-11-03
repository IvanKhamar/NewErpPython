"""Order pages: the list with filters, the order card, a new order (OrderList, OrderCard, NewOrder.razor)."""

import math
import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from starlette.datastructures import FormData

from newerp.api.delivery import Shipments
from newerp.api.deps import (
    Session,
    get_new_order_service,
    get_order_credit_service,
    get_order_queries,
    get_order_service,
    get_shipping_check_service,
)
from newerp.api.security import Policies
from newerp.application.orders.checks import ShippingCheckService
from newerp.application.orders.credits import OrderCreditService
from newerp.application.orders.models import (
    AssignManagerRequest,
    ChangeOrderStatusRequest,
    CreateOrderRequest,
    CustomerRiskQuery,
    NewOrderLineRequest,
    OrderCustomerDto,
    OrderDetails,
    OrderFilter,
    OrderMethodsDto,
    OrderStatusDto,
    ProductPickDto,
    ShippingCostLine,
    ShippingCostRequest,
    ShippingDateRequest,
    UpdateOrderCustomerRequest,
)
from newerp.application.orders.services import NewOrderService, OrderService
from newerp.domain.orders import AWAITING_SHIPMENT_STATUS_ID, DISCREPANCY_DAYS, SHIPPED_STATUS_ID
from newerp.infrastructure.orders.queries import SqlOrderQueries
from newerp.web.common import BUSINESS_ERRORS, WebUser, decimal_field, is_fragment, number, page_roles, render, undo

router = APIRouter(prefix="/app/orders", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))])
Queries = Annotated[SqlOrderQueries, Depends(get_order_queries)]
Orders = Annotated[OrderService, Depends(get_order_service)]
NewOrders = Annotated[NewOrderService, Depends(get_new_order_service)]
Checks = Annotated[ShippingCheckService, Depends(get_shipping_check_service)]
Credits = Annotated[OrderCreditService, Depends(get_order_credit_service)]
Field = Annotated[str, Form()]

PAGE_SIZE = 20
MAX_PRINTED = 50  # orders on one print page
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
        discrepancy_days=DISCREPANCY_DAYS,
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


def _create_request(form: FormData, methods: OrderMethodsDto) -> CreateOrderRequest:
    """The form fields as the same request the API takes as JSON. The lines come as repeated fields."""
    columns = [form.getlist(name) for name in ("product_id", "option_id", "quantity", "price")]
    lines = [
        NewOrderLineRequest(
            product_id=number(str(product)) or 0,
            product_option_value_id=number(str(option)),
            quantity=number(str(quantity)) or 0,
            price=decimal_field(str(price), "Ціна"),
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
        shipping_cost=decimal_field(_text(form, "shipping_cost"), "Доставка"),
        discount=decimal_field(_text(form, "discount"), "Знижка"),
        prepayment=decimal_field(_text(form, "prepayment"), "Передоплата"),
        comment=_text(form, "comment"),
    )


@router.post("/new")
async def create_order(request: Request, user: WebUser, service: NewOrders, session: Session) -> HTMLResponse:
    form = await request.form()
    try:
        result = await service.create(_create_request(form, await service.list_methods()), user.user_id)
    except BUSINESS_ERRORS as error:
        # Shown under the form, which keeps its values.
        return render(request, "_error.html", error=await undo(session, error))
    response = render(request, "orders/_created.html", result=result)
    # On success the whole form is replaced, so the same order cannot be sent twice.
    response.headers["HX-Retarget"] = "#new-order"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


def _cost_lines(form: FormData) -> tuple[list[ShippingCostLine], Decimal]:
    """The products of the form for the tariff, and their sum as the declared value."""
    lines: list[ShippingCostLine] = []
    value = Decimal(0)
    for product, quantity, price in zip(*(form.getlist(n) for n in ("product_id", "quantity", "price")), strict=True):
        count = number(str(quantity)) or 0
        if count > 0:
            lines.append(ShippingCostLine(product_id=number(str(product)) or 0, quantity=min(count, 1000)))
            value += max(decimal_field(str(price), "Ціна"), Decimal(0)) * count
    return lines, value


@router.post("/new/shipping-cost")
async def shipping_cost(request: Request, shipments: Shipments) -> HTMLResponse:
    """Replaces the shipping cost field with Nova Poshta's price; on a problem the field keeps its value."""
    form = await request.form()
    current = _text(form, "shipping_cost") or "0"
    try:
        lines, value = _cost_lines(form)
        cost_request = ShippingCostRequest(
            city=_text(form, "city")[:128], shipping_code=_text(form, "shipping_code")[:128], lines=lines[:100],
            cost=min(value, Decimal(10_000_000)),
        )  # fmt: skip
        cost = (await shipments.estimate_cost(cost_request)).cost
    except BUSINESS_ERRORS as error:
        return render(request, "orders/_shipping_cost.html", cost=current, error=str(error))
    return render(request, "orders/_shipping_cost.html", cost=f"{cost:.2f}", calculated=True)


@router.get("/discrepancies")
async def discrepancies(request: Request, checks: Checks, days: str = "") -> HTMLResponse:
    groups = await checks.discrepancies(number(days) or DISCREPANCY_DAYS)
    return render(request, "orders/_discrepancies.html", groups=groups)


@router.get("/print/{kind}")
async def print_orders(
    request: Request,
    queries: Queries,
    kind: Literal["invoice", "shipping"],
    ids: Annotated[list[int], Query(alias="id")],
) -> HTMLResponse:
    """The invoice or the shipping list of one or more orders (?id=1&id=2), one page each, for the browser's print."""
    orders = await queries.print_orders(ids[:MAX_PRINTED])
    if not orders:
        return render(request, "not_found.html", status_code=404)
    return render(request, f"orders/print_{kind}.html", orders=orders)


# ── Order card ──


async def _card(queries: SqlOrderQueries, order: OrderDetails) -> dict[str, Any]:
    statuses = await queries.list_statuses()
    return {
        "order": order,
        "statuses": statuses,
        "colors": _colors(statuses),
        "managers": await queries.list_managers(),
        "delete_roles": Policies.ORDER_DELETE,
    }


@router.get("/{order_id}")
async def order_card(request: Request, order_id: int, queries: Queries) -> HTMLResponse:
    order = await queries.get(order_id)
    if order is None:
        return render(request, "not_found.html", status_code=404)
    return render(request, "orders/card.html", **await _card(queries, order))


@router.post("/{order_id}/status")
async def change_status(
    request: Request,
    order_id: int,
    user: WebUser,
    queries: Queries,
    service: Orders,
    session: Session,
    checks: Checks,
    status_id: Field = "",
    confirmed: Field = "",
) -> HTMLResponse:
    """Before "Відправлено" and "Чекає на відправку" the waybill and SMS are checked first, as on the old admin;
    with problems the status changes only after the manager confirms."""
    warnings: list[str] = []
    error = None
    check = None
    new_status = number(status_id) or 0
    try:
        if confirmed != "1" and new_status in (SHIPPED_STATUS_ID, AWAITING_SHIPMENT_STATUS_ID):
            check = await checks.check(order_id, new_status)
        if check is None or not check.issues:
            check = None
            change = ChangeOrderStatusRequest(status_id=new_status)
            warnings = (await service.change_status(order_id, change, user.user_id)).warnings
    except BUSINESS_ERRORS as exc:
        error = await undo(session, exc)
    order = await queries.get(order_id)
    if order is None:
        return render(request, "not_found.html", status_code=404)
    context = await _card(queries, order)
    saved = error is None and check is None
    return render(request, "orders/_status.html", warnings=warnings, error=error, saved=saved, check=check, **context)


@router.post("/{order_id}/manager")
async def assign_manager(
    request: Request,
    order_id: int,
    user: WebUser,
    queries: Queries,
    service: Orders,
    session: Session,
    manager_id: Field = "",
) -> HTMLResponse:
    error = None
    try:
        await service.assign_manager(order_id, AssignManagerRequest(manager_id=number(manager_id) or 0), user.user_id)
    except BUSINESS_ERRORS as exc:
        error = await undo(session, exc)
    order = await queries.get(order_id)
    if order is None:
        return render(request, "not_found.html", status_code=404)
    return render(request, "orders/_manager.html", error=error, saved=error is None, **await _card(queries, order))


def _customer_request(form: FormData, methods: OrderMethodsDto) -> UpdateOrderCustomerRequest:
    """The method names come from the known methods by code, like for a new order, not from the browser."""
    shipping = {method.code: method.name for method in methods.shipping}
    payment = {method.code: method.name for method in methods.payment}
    fields = "firstname lastname telephone email instagram city address1 address2 house flat comment".split()
    return UpdateOrderCustomerRequest(
        **{name: _text(form, name) for name in fields},
        shipping_code=_text(form, "shipping_code"),
        shipping_method=shipping.get(_text(form, "shipping_code"), ""),
        payment_code=_text(form, "payment_code"),
        payment_method=payment.get(_text(form, "payment_code"), ""),
    )


def _too_long(error: ValidationError) -> str:
    names = ", ".join(str(item["loc"][0]) for item in error.errors())
    return f"Задовге значення: {names}."


@router.get("/{order_id}/customer/edit")
async def customer_form(request: Request, order_id: int, service: NewOrders) -> HTMLResponse:
    customer = await service.get_customer(order_id)
    return render(request, "orders/_customer_form.html", customer=customer, methods=await service.list_methods())


@router.post("/{order_id}/customer")
async def update_customer(
    request: Request, order_id: int, user: WebUser, queries: Queries, service: NewOrders, session: Session
) -> HTMLResponse:
    form = await request.form()
    methods = await service.list_methods()
    try:
        update = _customer_request(form, methods)
        await service.update_customer(order_id, update, user.user_id)
    except ValidationError as exc:
        error = _too_long(exc)
    except BUSINESS_ERRORS as exc:
        error = await undo(session, exc)
    else:
        order = await queries.get(order_id)
        if order is None:
            return render(request, "not_found.html", status_code=404)
        response = render(request, "orders/_customer.html", order=order, saved=True)
        # The form replaces itself on an error; on success the whole section shows the saved data.
        response.headers["HX-Retarget"] = "#customer"
        response.headers["HX-Reswap"] = "innerHTML"
        return response
    # The form stays with what was typed, and the error under it.
    typed = {name: _text(form, name) for name in OrderCustomerDto.model_fields if name != "order_id"}
    customer = OrderCustomerDto(order_id=order_id, **typed)
    return render(request, "orders/_customer_form.html", customer=customer, methods=methods, error=error)


@router.post("/{order_id}/shipping-date")
async def set_shipping_date(
    request: Request, order_id: int, queries: Queries, service: Orders, session: Session, date_shipping: Field = ""
) -> HTMLResponse:
    error = None
    try:
        day = date.fromisoformat(date_shipping) if date_shipping else None
        await service.set_shipping_date(order_id, ShippingDateRequest(date_shipping=day))
    except ValueError:
        error = "Дата має бути у форматі РРРР-ММ-ДД."
    except BUSINESS_ERRORS as exc:
        error = await undo(session, exc)
    order = await queries.get(order_id)
    if order is None:
        return render(request, "not_found.html", status_code=404)
    return render(request, "orders/_customer.html", order=order, error=error, date_saved=error is None)


@router.post("/{order_id}/invoice")
async def create_invoice(
    request: Request, order_id: int, queries: Queries, service: Orders, session: Session
) -> HTMLResponse:
    error = None
    try:
        await service.create_invoice(order_id)
    except BUSINESS_ERRORS as exc:
        error = await undo(session, exc)
    order = await queries.get(order_id)
    if order is None:
        return render(request, "not_found.html", status_code=404)
    return render(request, "orders/_documents.html", error=error, **await _card(queries, order))


@router.get("/{order_id}/credits")
async def credits_section(request: Request, order_id: int, credits: Credits) -> HTMLResponse:
    return render(request, "orders/_credits.html", credits=await credits.get(order_id))


@router.post("/{order_id}/credits/{action}")
async def change_credits(
    request: Request,
    order_id: int,
    action: Literal["add-reward", "remove-reward", "add-commission", "remove-commission"],
    user: WebUser,
    credits: Credits,
    session: Session,
) -> HTMLResponse:
    change = {
        "add-reward": credits.add_reward,
        "remove-reward": credits.remove_reward,
        "add-commission": credits.add_commission,
        "remove-commission": credits.remove_commission,
    }[action]
    error = None
    try:
        await change(order_id, user.user_id)
    except BUSINESS_ERRORS as exc:
        error = await undo(session, exc)
    return render(request, "orders/_credits.html", credits=await credits.get(order_id), error=error)


@router.post("/{order_id}/delete", dependencies=[Depends(page_roles(*Policies.ORDER_DELETE))])
async def delete_order(request: Request, order_id: int, user: WebUser, service: Orders, session: Session) -> Response:
    try:
        await service.delete(order_id, user.user_id)
    except BUSINESS_ERRORS as exc:
        return render(request, "_error.html", error=await undo(session, exc))
    # HTMX follows HX-Redirect with a full page load; the card of a deleted order no longer exists.
    return Response(status_code=204, headers={"HX-Redirect": "/app/orders"})
