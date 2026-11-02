"""Bank payment pages: the statement with matching to orders, and the payments and LiqPay link of an order
(BankPayments.razor, MatchPaymentDialog.razor, LiqPayPanel.razor)."""

import math
from datetime import date
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from newerp.api.deps import Session
from newerp.api.payments import Bank, LiqPay
from newerp.api.security import Policies
from newerp.application.payments import BankPaymentFilter, CreatePaymentLinkRequest, LinkPaymentRequest
from newerp.domain.payments import MatchReason
from newerp.web.common import BUSINESS_ERRORS, decimal_field, is_fragment, number, page_roles, render, undo

router = APIRouter(
    prefix="/app/payments", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.FINANCE))]
)
Field = Annotated[str, Form()]

PAGE_SIZE = 30
REASONS = {
    MatchReason.ORDER_NUMBER: "номер у призначенні",
    MatchReason.TOTAL: "сума",
    MatchReason.PREPAYMENT: "завдаток",
    MatchReason.PHONE: "телефон",
    MatchReason.NAME: "прізвище",
}


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@router.get("")
async def payment_list(
    request: Request,
    bank: Bank,
    linked: str = "",
    search: str = "",
    hidden: str = "",
    since: str = "",
    until: str = "",
    page: str = "1",
) -> HTMLResponse:
    current = max(number(page) or 1, 1)
    payment_filter = BankPaymentFilter(
        from_=_date(since),
        to=_date(until),
        linked={"yes": True, "no": False}.get(linked),
        search=search.strip() or None,
        hidden=bool(hidden),
        page=current,
        page_size=PAGE_SIZE,
    )
    result = await bank.list_payments(payment_filter)
    pages = max(1, math.ceil(result.total_count / PAGE_SIZE))

    def link(to_page: int) -> str:
        fields = {"linked": linked, "search": search, "hidden": hidden, "since": since, "until": until, "page": to_page}
        return urlencode({key: value for key, value in fields.items() if value})

    context = {
        "result": result,
        "pages": pages,
        "previous_page": link(current - 1) if current > 1 else None,
        "next_page": link(current + 1) if current < pages else None,
    }
    if is_fragment(request):
        return render(request, "payments/_table.html", **context)
    values = {"linked": linked, "search": search, "hidden": hidden, "since": since, "until": until}
    return render(request, "payments/list.html", values=values, **context)


@router.post("/import")
async def import_statement(request: Request, bank: Bank, since: Field = "", until: Field = "") -> HTMLResponse:
    try:
        result = await bank.import_statement(_date(since), _date(until))
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=str(error))
    if not result.client_configured:
        text = "Ключів ПриватБанку немає: виписку не завантажено."
    else:
        text = f"Отримано {result.fetched}, нових {result.added}, уже були {result.skipped}. Оновіть сторінку."
    return render(request, "_note.html", text=text)


def _row_error(request: Request, payment_id: int, error: str) -> HTMLResponse:
    """A refused link or hide: the message goes under the row instead of replacing it."""
    response = render(request, "_error.html", error=error)
    response.headers["HX-Retarget"] = f"#match-{payment_id}"
    response.headers["HX-Reswap"] = "innerHTML"
    return response


@router.get("/{payment_id}/suggestions")
async def suggestions(request: Request, payment_id: int, bank: Bank, customer: str = "") -> HTMLResponse:
    """The orders this payment likely belongs to, best first, with what matched (MatchPaymentDialog)."""
    try:
        found = await bank.suggest(payment_id, customer.strip() or None)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=str(error))
    rows = [(s, [text for flag, text in REASONS.items() if flag in s.reasons]) for s in found]
    return render(request, "payments/_suggestions.html", payment_id=payment_id, rows=rows, customer=customer)


@router.post("/{payment_id}/order")
async def link_order(
    request: Request, payment_id: int, bank: Bank, session: Session, order_id: Field = ""
) -> HTMLResponse:
    """Link to an order, or unlink with an empty order; the answer is the payment's row."""
    try:
        payment = await bank.link(payment_id, LinkPaymentRequest(order_id=number(order_id) or 0))
    except BUSINESS_ERRORS as error:
        return _row_error(request, payment_id, await undo(session, error))
    return render(request, "payments/_row.html", payment=payment)


@router.post("/{payment_id}/visible")
async def set_visible(
    request: Request, payment_id: int, bank: Bank, session: Session, visible: Field = ""
) -> HTMLResponse:
    try:
        await bank.set_visibility(payment_id, visible == "1")
    except BUSINESS_ERRORS as error:
        return _row_error(request, payment_id, await undo(session, error))
    return HTMLResponse("")  # the row leaves the list it was in


# ── The payments of an order (a section of the order card) ──


async def _order_payments(request: Request, order_id: int, bank: Bank, liqpay: LiqPay, **context: Any):
    return render(
        request,
        "payments/_order.html",
        order_id=order_id,
        payments=await bank.for_order(order_id),
        status=await liqpay.status(order_id),
        **context,
    )


@router.get("/orders/{order_id}")
async def order_payments(request: Request, order_id: int, bank: Bank, liqpay: LiqPay) -> HTMLResponse:
    try:
        return await _order_payments(request, order_id, bank, liqpay)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=str(error))


@router.post("/orders/{order_id}/liqpay-link")
async def liqpay_link(request: Request, order_id: int, bank: Bank, liqpay: LiqPay, amount: Field = "") -> HTMLResponse:
    """A LiqPay payment link to send to the customer; an empty amount is what is left to pay."""
    try:
        sum_ = decimal_field(amount, "Сума") if amount.strip() else None
        link = await liqpay.create_link(order_id, CreatePaymentLinkRequest(amount=sum_))
    except BUSINESS_ERRORS as error:
        return await _order_payments(request, order_id, bank, liqpay, error=str(error))
    return await _order_payments(request, order_id, bank, liqpay, link=link)
