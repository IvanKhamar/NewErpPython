"""Marketing pages: birthday greetings by SMS, the contact export, gift vouchers and the voucher of an order
(Birthdays.razor, ContactExport.razor, Vouchers.razor, VoucherPanel.razor)."""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import FormData

from newerp.api.deps import Session
from newerp.api.marketing import Marketing, Vouchers, contacts_csv
from newerp.api.security import Policies
from newerp.application.marketing.campaigns import BirthdaySmsRequest, BulkSmsRequest
from newerp.application.marketing.vouchers import SaveVoucherRequest
from newerp.domain.marketing import BirthdayPeriod
from newerp.web.common import BUSINESS_ERRORS, Redirect, decimal_field, number, page_roles, qty, render, undo

router = APIRouter(
    prefix="/app/marketing", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))]
)
Field = Annotated[str, Form()]

PERIODS = {
    BirthdayPeriod.TODAY: "Сьогодні",
    BirthdayPeriod.TOMORROW: "Завтра",
    BirthdayPeriod.WEEK: "Тиждень",
    BirthdayPeriod.MONTH: "Цей місяць",
    BirthdayPeriod.ALL: "Усі",
}


# ── Birthdays ──


@router.get("/birthdays")
async def birthdays(request: Request, marketing: Marketing, period: str = "0") -> HTMLResponse:
    try:
        chosen = BirthdayPeriod(number(period) or 0)
    except ValueError:
        chosen = BirthdayPeriod.TODAY
    contacts = await marketing.birthdays(chosen)
    return render(request, "marketing/birthdays.html", contacts=contacts, periods=PERIODS, period=chosen)


@router.get("/birthdays/greet")
async def greeting_form(request: Request, marketing: Marketing, phone: str = "", name: str = "") -> HTMLResponse:
    """The greeting panel of one customer, with the orders by that phone."""
    return render(request, "marketing/_greet.html", phone=phone, name=name,
                  orders=await marketing.customer_orders(phone))  # fmt: skip


@router.post("/birthdays/coupon-code")
async def coupon_code(request: Request, marketing: Marketing) -> HTMLResponse:
    code = (await marketing.new_coupon_code()).code
    return render(request, "marketing/_coupon.html", code=code)


@router.post("/birthdays/sms")
async def birthday_sms(
    request: Request, marketing: Marketing, session: Session, phone: Field = "", text: Field = "",
    coupon_code: Field = "",
) -> HTMLResponse:  # fmt: skip
    try:
        sms = BirthdaySmsRequest(phone=phone, text=text, coupon_code=coupon_code.strip() or None)
        result = await marketing.send_birthday_sms(sms)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    if not result.sent:
        return render(request, "_error.html", error=f"SMS не надіслано: {result.response}")
    note = " з купоном" if result.coupon_id else ""
    return render(request, "_note.html", text=f"Привітання{note} надіслано.")


@router.post("/birthdays/sms-bulk")
async def bulk_sms(request: Request, marketing: Marketing, session: Session) -> HTMLResponse:
    form = await request.form()
    try:
        phones = [str(phone) for phone in form.getlist("phones")]
        result = await marketing.send_bulk(BulkSmsRequest(phones=phones, text=str(form.get("text", ""))))
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    skipped = f", пропущено: {', '.join(result.skipped)}" if result.skipped else ""
    return render(request, "_note.html", text=f"Надіслано {result.sent}, не надіслано {result.not_sent}{skipped}.")


# ── Contact export ──


@router.get("/export")
async def export_page(request: Request) -> HTMLResponse:
    return render(request, "marketing/export.html")


@router.get("/export/contacts.csv")
async def export_csv(
    request: Request, marketing: Marketing, field: str = "Phone", since: str = "", until: str = ""
) -> Response:
    """The same CSV the API gives; the sources are repeated "sources" fields of the form."""
    sources = [str(source) for source in request.query_params.getlist("sources")]
    try:
        return await contacts_csv(marketing, field, sources, _date(since), _date(until))
    except (ValueError, *BUSINESS_ERRORS) as error:
        return render(request, "print_error.html", status_code=400, error=str(error))


def _date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


# ── Gift vouchers ──


@router.get("/vouchers")
async def voucher_list(request: Request, vouchers: Vouchers, search: str = "") -> HTMLResponse:
    cards = await vouchers.list_cards(search.strip() or None)
    template = "marketing/_vouchers.html" if "HX-Request" in request.headers else "marketing/vouchers.html"
    return render(request, template, vouchers=cards, search=search)


def _text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _voucher_request(form: FormData) -> SaveVoucherRequest:
    fields = ("code", "from_name", "from_email", "to_name", "to_email", "message")
    return SaveVoucherRequest(
        **{name: _text(form, name) for name in fields},
        theme_id=number(_text(form, "theme_id")),
        amount=decimal_field(_text(form, "amount"), "Сума"),
        active=form.get("active") == "1",
    )


async def _voucher_form(request: Request, vouchers: Vouchers, values: dict[str, Any], voucher_id: int | None = None,
                        error: str | None = None, **context: Any) -> HTMLResponse:  # fmt: skip
    return render(request, "marketing/voucher.html", status_code=200 if error is None else 400, values=values,
                  voucher_id=voucher_id, themes=await vouchers.themes(), error=error, **context)  # fmt: skip


@router.get("/vouchers/new")
async def new_voucher_page(request: Request, vouchers: Vouchers) -> HTMLResponse:
    return await _voucher_form(request, vouchers, {"active": "1"})


@router.get("/vouchers/{voucher_id}")
async def voucher_page(request: Request, voucher_id: int, vouchers: Vouchers) -> HTMLResponse:
    try:
        card = await vouchers.get(voucher_id)
    except BUSINESS_ERRORS:
        return render(request, "not_found.html", status_code=404)
    values = card.model_dump() | {"amount": qty(card.amount), "active": "1" if card.active else ""}
    return await _voucher_form(request, vouchers, values, voucher_id, card=card)


@router.post("/vouchers/save")
async def save_voucher(request: Request, vouchers: Vouchers, session: Session) -> HTMLResponse:
    form = await request.form()
    voucher_id = number(_text(form, "voucher_id"))
    try:
        if voucher_id is None:
            card = await vouchers.create(_voucher_request(form))
        else:
            card = await vouchers.update(voucher_id, _voucher_request(form))
    except BUSINESS_ERRORS as error:
        return await _voucher_form(request, vouchers, dict(form), voucher_id, await undo(session, error))
    raise Redirect(f"/app/marketing/vouchers/{card.id}")


@router.post("/vouchers/{voucher_id}/delete")
async def delete_voucher(request: Request, voucher_id: int, vouchers: Vouchers, session: Session) -> HTMLResponse:
    try:
        await vouchers.delete(voucher_id)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    raise Redirect("/app/marketing/vouchers")


@router.post("/vouchers/{voucher_id}/sms")
async def voucher_sms(request: Request, voucher_id: int, vouchers: Vouchers, phone: Field = "") -> HTMLResponse:
    try:
        result = await vouchers.send_sms(voucher_id, phone)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=str(error))
    if not result.sent:
        return render(request, "_error.html", error=f"SMS не надіслано: {result.response}")
    return render(request, "_note.html", text="Код сертифіката надіслано.")


# ── The voucher of an order (a section of the order card) ──


async def _order_voucher(request: Request, order_id: int, vouchers: Vouchers, **messages: str | None):
    return render(request, "marketing/_order_voucher.html", order_id=order_id,
                  spent=await vouchers.for_order(order_id), **messages)  # fmt: skip


@router.get("/orders/{order_id}/voucher")
async def order_voucher(request: Request, order_id: int, vouchers: Vouchers) -> HTMLResponse:
    return await _order_voucher(request, order_id, vouchers)


@router.post("/orders/{order_id}/voucher/{action}")
async def order_voucher_action(
    request: Request, order_id: int, action: str, vouchers: Vouchers, session: Session, code: Field = "",
    amount: Field = "",
) -> HTMLResponse:  # fmt: skip
    """Pay a part of the order with a voucher, or take the voucher off (the money goes back to it)."""
    try:
        if action == "redeem":
            spent = decimal_field(amount, "Сума") if amount.strip() else None
            await vouchers.redeem(order_id, code.strip(), spent)
            saved = "Сертифікат списано, сума замовлення зменшилась."
        elif action == "release":
            await vouchers.release(order_id)
            saved = "Сертифікат знято, гроші повернулися на нього."
        else:
            return render(request, "not_found.html", status_code=404)
    except BUSINESS_ERRORS as error:
        return await _order_voucher(request, order_id, vouchers, error=await undo(session, error))
    return await _order_voucher(request, order_id, vouchers, saved=saved)
