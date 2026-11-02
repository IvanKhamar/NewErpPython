"""Customer pages: the list and the card with orders, risk and the managers' notes (Pages/Customers)."""

import math
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from newerp.api.customers import Service
from newerp.api.deps import Session
from newerp.api.security import Policies
from newerp.application.customers import AddCustomerNoteRequest, CustomerFilter
from newerp.web.common import BUSINESS_ERRORS, is_fragment, number, page_roles, render, undo

router = APIRouter(
    prefix="/app/customers", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))]
)

PAGE_SIZE = 20


@router.get("")
async def customer_list(
    request: Request, service: Service, search: str = "", group: str = "", page: str = "1"
) -> HTMLResponse:
    current = max(number(page) or 1, 1)
    customer_filter = CustomerFilter(
        search=search.strip() or None, group_id=number(group), page=current, page_size=PAGE_SIZE
    )
    result = await service.list_page(customer_filter)
    pages = max(1, math.ceil(result.total_count / PAGE_SIZE))

    def link(to_page: int) -> str:
        fields = {"search": search, "group": group, "page": to_page}
        return urlencode({key: value for key, value in fields.items() if value})

    context = {
        "result": result,
        "pages": pages,
        "previous_page": link(current - 1) if current > 1 else None,
        "next_page": link(current + 1) if current < pages else None,
    }
    if is_fragment(request):
        return render(request, "customers/_table.html", **context)
    return render(request, "customers/list.html", groups=await service.groups(), group_id=number(group),
                  search=search, **context)  # fmt: skip


async def _card(request: Request, customer_id: int, service: Service, template: str, **messages: str | None):
    try:
        customer = await service.get(customer_id)
    except BUSINESS_ERRORS:
        return render(request, "not_found.html", status_code=404)
    return render(request, template, customer=customer, **messages)


@router.get("/{customer_id}")
async def customer_card(request: Request, customer_id: int, service: Service) -> HTMLResponse:
    return await _card(request, customer_id, service, "customers/card.html")


@router.post("/{customer_id}/notes")
async def add_note(
    request: Request, customer_id: int, service: Service, session: Session, comment: Annotated[str, Form()] = ""
) -> HTMLResponse:
    try:
        await service.add_note(customer_id, AddCustomerNoteRequest(comment=comment))
    except BUSINESS_ERRORS as error:
        return await _card(request, customer_id, service, "customers/_notes.html", error=await undo(session, error))
    return await _card(request, customer_id, service, "customers/_notes.html", saved="Нотатку додано.")
