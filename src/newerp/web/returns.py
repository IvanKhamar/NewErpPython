"""Return pages: the list, the card with its status log, a new return from an order card (Pages/Returns)."""

import math
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from newerp.api.deps import Session, get_order_queries
from newerp.api.returns import Service
from newerp.api.security import Policies
from newerp.application.returns import AddReturnHistoryRequest, CreateReturnRequest, ReturnFilter
from newerp.infrastructure.orders.queries import SqlOrderQueries
from newerp.web.common import BUSINESS_ERRORS, Redirect, is_fragment, number, page_roles, render, undo

router = APIRouter(prefix="/app/returns", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))])
Queries = Annotated[SqlOrderQueries, Depends(get_order_queries)]
Field = Annotated[str, Form()]

PAGE_SIZE = 20


@router.get("")
async def return_list(
    request: Request, service: Service, status: str = "", search: str = "", page: str = "1"
) -> HTMLResponse:
    current = max(number(page) or 1, 1)
    return_filter = ReturnFilter(
        status_id=number(status), search=search.strip() or None, page=current, page_size=PAGE_SIZE
    )
    result = await service.list_page(return_filter)
    pages = max(1, math.ceil(result.total_count / PAGE_SIZE))

    def link(to_page: int) -> str:
        fields = {"status": status, "search": search, "page": to_page}
        return urlencode({key: value for key, value in fields.items() if value})

    context = {
        "result": result,
        "pages": pages,
        "previous_page": link(current - 1) if current > 1 else None,
        "next_page": link(current + 1) if current < pages else None,
    }
    if is_fragment(request):
        return render(request, "returns/_table.html", **context)
    lookups = await service.lookups()
    return render(request, "returns/list.html", statuses=lookups.statuses, status_id=number(status), search=search,
                  **context)  # fmt: skip


@router.get("/new")
async def new_return_form(request: Request, queries: Queries, service: Service, order: str = "") -> HTMLResponse:
    """The form inside the order card; it loads when the "Повернення товару" section is opened."""
    details = await queries.get(number(order) or 0)
    if details is None:
        return render(request, "not_found.html", status_code=404)
    return render(request, "returns/_new.html", order=details, lookups=await service.lookups())


@router.post("/new")
async def create_return(request: Request, service: Service, session: Session) -> HTMLResponse:
    form = await request.form()

    def field(name: str) -> int:
        value = form.get(name, "")
        return number(value.strip() if isinstance(value, str) else "") or 0

    try:
        comment = form.get("comment", "")
        create = CreateReturnRequest(
            order_id=field("order_id"),
            product_id=field("product_id"),
            quantity=field("quantity"),
            reason_id=field("reason_id"),
            status_id=field("status_id"),
            action_id=field("action_id"),
            opened=form.get("opened") == "1",
            comment=comment if isinstance(comment, str) else None,
        )
        return_id = await service.create(create)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    raise Redirect(f"/app/returns/{return_id}")


async def _card(request: Request, return_id: int, service: Service, template: str, **messages: str | None):
    try:
        details = await service.get(return_id)
    except BUSINESS_ERRORS:
        return render(request, "not_found.html", status_code=404)
    return render(request, template, item=details, lookups=await service.lookups(), **messages)


@router.get("/{return_id}")
async def return_card(request: Request, return_id: int, service: Service) -> HTMLResponse:
    return await _card(request, return_id, service, "returns/card.html")


@router.post("/{return_id}/history")
async def add_history(
    request: Request,
    return_id: int,
    service: Service,
    session: Session,
    status_id: Field = "",
    action_id: Field = "",
    comment: Field = "",
) -> HTMLResponse:
    try:
        change = AddReturnHistoryRequest(status_id=number(status_id) or 0, comment=comment, action_id=number(action_id))
        await service.add_history(return_id, change)
    except BUSINESS_ERRORS as error:
        return await _card(request, return_id, service, "returns/_card.html", error=await undo(session, error))
    return await _card(request, return_id, service, "returns/_card.html", saved="Статус змінено.")
