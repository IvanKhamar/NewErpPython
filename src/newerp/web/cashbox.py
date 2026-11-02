"""Cashbox pages: cashiers and shifts, X and Z reports, cheques, the admin's setup, and the cheque of an order
(Cashbox.razor). Checkbox runs as a stub without its keys."""

from datetime import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import FormData

from newerp.api.cashbox import Service
from newerp.api.deps import Session
from newerp.api.security import Policies
from newerp.application.cashbox.models import (
    ChequeFilter,
    IssueChequeRequest,
    SaveCashierRequest,
    SaveDirectoryEntryRequest,
    WorkShiftFilter,
)
from newerp.domain.common import DomainError
from newerp.web.common import BUSINESS_ERRORS, Redirect, WebUser, number, page_roles, render, undo

router = APIRouter(
    prefix="/app/cashbox", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.FINANCE))]
)
setup_only = [Depends(page_roles(*Policies.FINANCE_SETUP))]
Field = Annotated[str, Form()]


async def _page(request: Request, service: Service, user: WebUser, template: str, **context: Any) -> HTMLResponse:
    return render(
        request,
        template,
        setup=await service.setup(),
        shifts=(await service.shifts(WorkShiftFilter()))[:20],
        reports=(await service.reports(None, None, None))[:20],
        cheques=await service.cheques(ChequeFilter(page=1, page_size=30)),
        can_setup=user.role in Policies.FINANCE_SETUP,
        **context,
    )


@router.get("")
async def cashbox_page(request: Request, service: Service, user: WebUser) -> HTMLResponse:
    return await _page(request, service, user, "cashbox/page.html")


@router.post("/cashiers/{cashier_id}/{action}")
async def shift_action(
    request: Request, cashier_id: int, action: str, user: WebUser, service: Service, session: Session
) -> HTMLResponse:
    """Open or close the shift of a cashier, or show the X report (the summary without closing)."""
    report_text = None
    try:
        if action == "open":
            await service.open_shift(cashier_id, user.user_id)
            saved = "Зміну відкрито."
        elif action == "close":
            closed = await service.close_shift(cashier_id, user.user_id)
            saved = "Зміну закрито, Z-звіт збережено." if closed else "Checkbox ще закриває зміну; звірте її пізніше."
        elif action == "x-report":
            report_text = (await service.x_report(cashier_id)).text
            saved = None
        else:
            return render(request, "not_found.html", status_code=404)
    except BUSINESS_ERRORS as error:
        return await _page(request, service, user, "cashbox/_main.html", error=await undo(session, error))
    return await _page(request, service, user, "cashbox/_main.html", saved=saved, report_text=report_text)


@router.post("/shifts/{shift_id}/sync")
async def sync_shift(request: Request, shift_id: int, user: WebUser, service: Service, session: Session):
    try:
        report = await service.sync_shift(shift_id, user.user_id)
    except BUSINESS_ERRORS as error:
        return await _page(request, service, user, "cashbox/_main.html", error=await undo(session, error))
    saved = "Зміну закрито в Checkbox, Z-звіт збережено." if report else "Зміна в Checkbox ще відкрита."
    return await _page(request, service, user, "cashbox/_main.html", saved=saved)


@router.get("/reports/{report_id}/text")
async def report_text(request: Request, report_id: int, service: Service) -> HTMLResponse:
    try:
        text = (await service.z_report_text(report_id)).text
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=str(error))
    return render(request, "cashbox/_report_text.html", text=text)


@router.post("/cheques/{cheque_id}/{action}")
async def cheque_action(
    request: Request, cheque_id: int, action: str, user: WebUser, service: Service, session: Session
) -> HTMLResponse:
    try:
        if action == "fiscalize":
            await service.fiscalize(cheque_id)
            saved = "Чек фіскалізовано."
        elif action == "delete":
            await service.delete_cheque(cheque_id)
            saved = "Чек видалено."
        else:
            return render(request, "not_found.html", status_code=404)
    except BUSINESS_ERRORS as error:
        return await _page(request, service, user, "cashbox/_main.html", error=await undo(session, error))
    return await _page(request, service, user, "cashbox/_main.html", saved=saved)


# ── Setup (Admin): cashiers, warehouses (points of sale), requisites ──


def _text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _cashier_request(form: FormData) -> SaveCashierRequest:
    closes = _text(form, "auto_close_time")
    try:
        auto_close = time.fromisoformat(closes) if closes else None
    except ValueError:
        raise DomainError("Час автозакриття: потрібен формат 21:30.") from None
    return SaveCashierRequest(
        name=_text(form, "name"),
        description=_text(form, "description"),
        payment_code=_text(form, "payment_code"),
        auto_close_time=auto_close,
        warehouse_id=number(_text(form, "warehouse_id")) or 0,
        requisites_id=number(_text(form, "requisites_id")) or 0,
        is_active=form.get("is_active") == "1",
    )


async def _cashier_form(
    request: Request, service: Service, values: dict[str, Any], cashier_id: int | None = None, error: str | None = None
) -> HTMLResponse:
    setup = await service.setup()
    return render(request, "cashbox/cashier_form.html", status_code=200 if error is None else 400, values=values,
                  cashier_id=cashier_id, setup=setup, error=error)  # fmt: skip


@router.get("/cashiers/new", dependencies=setup_only)
async def new_cashier_page(request: Request, service: Service) -> HTMLResponse:
    return await _cashier_form(request, service, {"is_active": "1"})


@router.get("/cashiers/{cashier_id}/edit", dependencies=setup_only)
async def edit_cashier_page(request: Request, cashier_id: int, service: Service) -> HTMLResponse:
    cashier = next((c for c in (await service.setup()).cashiers if c.id == cashier_id), None)
    if cashier is None:
        return render(request, "not_found.html", status_code=404)
    values = {
        "name": cashier.name,
        "description": cashier.description,
        "payment_code": cashier.payment_code,
        "auto_close_time": f"{cashier.auto_close_time:%H:%M}" if cashier.auto_close_time else "",
        "warehouse_id": str(cashier.warehouse_id),
        "requisites_id": str(cashier.requisites_id),
        "is_active": "1" if cashier.is_active else "",
    }
    return await _cashier_form(request, service, values, cashier_id)


@router.post("/cashiers/save", dependencies=setup_only)
async def save_cashier(request: Request, service: Service, session: Session) -> HTMLResponse:
    form = await request.form()
    cashier_id = number(_text(form, "cashier_id"))
    try:
        if cashier_id is None:
            await service.create_cashier(_cashier_request(form))
        else:
            await service.update_cashier(cashier_id, _cashier_request(form))
    except BUSINESS_ERRORS as error:
        return await _cashier_form(request, service, dict(form), cashier_id, await undo(session, error))
    raise Redirect("/app/cashbox")


@router.post("/directory/{kind}", dependencies=setup_only)
async def add_entry(
    request: Request, kind: str, user: WebUser, service: Service, session: Session, name: Field = "",
    description: Field = "",
) -> HTMLResponse:  # fmt: skip
    """A new warehouse (point of sale) or requisites (a sole trader's details)."""
    entry = SaveDirectoryEntryRequest(name=name, description=description)
    try:
        if kind == "warehouses":
            await service.create_warehouse(entry)
        elif kind == "requisites":
            await service.create_requisites(entry)
        else:
            return render(request, "not_found.html", status_code=404)
    except BUSINESS_ERRORS as error:
        return await _page(request, service, user, "cashbox/_main.html", error=await undo(session, error))
    return await _page(request, service, user, "cashbox/_main.html", saved="Додано.")


@router.post("/directory/{kind}/{entry_id}/delete", dependencies=setup_only)
async def delete_entry(
    request: Request, kind: str, entry_id: int, user: WebUser, service: Service, session: Session
) -> HTMLResponse:
    try:
        if kind == "warehouses":
            await service.delete_warehouse(entry_id)
        elif kind == "requisites":
            await service.delete_requisites(entry_id)
        else:
            return render(request, "not_found.html", status_code=404)
    except BUSINESS_ERRORS as error:
        return await _page(request, service, user, "cashbox/_main.html", error=await undo(session, error))
    return await _page(request, service, user, "cashbox/_main.html", saved="Видалено.")


# ── The cheque of an order (a section of the order card) ──


async def _cheque(request: Request, order_id: int, service: Service, **messages: str | None) -> HTMLResponse:
    cheque = await service.cheque_for_order(order_id)
    cashiers = [c for c in (await service.setup()).cashiers if c.is_active] if cheque is None else []
    return render(request, "cashbox/_cheque.html", order_id=order_id, cheque=cheque, cashiers=cashiers, **messages)


@router.get("/orders/{order_id}/cheque")
async def cheque_section(request: Request, order_id: int, service: Service) -> HTMLResponse:
    return await _cheque(request, order_id, service)


@router.post("/orders/{order_id}/cheque")
async def issue_cheque(
    request: Request, order_id: int, service: Service, session: Session, cashier_id: Field = ""
) -> HTMLResponse:
    try:
        await service.issue_for_order(order_id, IssueChequeRequest(cashier_id=number(cashier_id)))
    except BUSINESS_ERRORS as error:
        return await _cheque(request, order_id, service, error=await undo(session, error))
    return await _cheque(request, order_id, service, saved="Чек видано.")
