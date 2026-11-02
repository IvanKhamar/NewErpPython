"""Delivery pages: tracking and pickup reminders, and the Nova Poshta waybill of an order (Delivery.razor and
CreateShipmentDialog.razor). Waybills abroad are made through the API for now."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import FormData

from newerp.api.delivery import Delivery, Shipments
from newerp.api.deps import Session
from newerp.api.security import Policies
from newerp.application.delivery.models import (
    SEAT_PRESETS,
    CreateShipmentRequest,
    ShipmentPrintKind,
    ShipmentSeat,
)
from newerp.infrastructure.delivery.repositories import SqlShipmentRepository
from newerp.web.common import BUSINESS_ERRORS, WebUser, decimal_field, page_roles, render, undo

router = APIRouter(
    prefix="/app/delivery", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))]
)

# The tone of a waybill status as a chip colour (ShipmentTone: info, success, warning, error).
TONES = ("ordered", "received", "few", "none")


@router.get("")
async def delivery_page(request: Request, delivery: Delivery) -> HTMLResponse:
    return render(request, "delivery/page.html", reminders=await delivery.reminders())


@router.post("/tracking/refresh")
async def refresh_tracking(request: Request, delivery: Delivery) -> HTMLResponse:
    result = await delivery.refresh_tracking()
    if not result.client_configured:
        text = "Ключ API Нової Пошти не налаштовано: статуси не оновлено."
    else:
        text = f"Перевірено ТТН: {result.checked}, змінилося: {result.updated}."
    return render(request, "_note.html", text=text)


@router.post("/warehouses/refresh")
async def refresh_warehouses(request: Request, shipments: Shipments) -> HTMLResponse:
    result = await shipments.refresh_warehouses()
    if not result.client_configured:
        text = "Ключ API Нової Пошти не налаштовано: довідник не оновлено."
    else:
        text = f"Довідник оновлено: відділень і поштоматів {result.count}."
    return render(request, "_note.html", text=text)


@router.post("/reminders/{order_id}/done")
async def reminded(request: Request, order_id: int, delivery: Delivery, session: Session) -> HTMLResponse:
    error = None
    try:
        await delivery.mark_reminded(order_id)
    except BUSINESS_ERRORS as exc:
        error = await undo(session, exc)
    return render(request, "delivery/_reminders.html", reminders=await delivery.reminders(), error=error)


# ── Directory pickers of the waybill form ──


@router.get("/cities")
async def cities(request: Request, shipments: Shipments, city_search: str = "") -> HTMLResponse:
    found = await shipments.search_cities(city_search) if len(city_search.strip()) >= 2 else []
    return render(request, "delivery/_cities.html", cities=found, autoload=True)


@router.get("/warehouses")
async def warehouses(
    request: Request, shipments: Shipments, city_ref: str = "", warehouse_search: str = ""
) -> HTMLResponse:
    found = await shipments.search_warehouses(city_ref, warehouse_search.strip() or None) if city_ref else []
    return render(request, "delivery/_warehouses.html", warehouses=found)


# ── The waybill of an order (a section of the order card) ──


async def _shipment(request: Request, order_id: int, session: Session, shipments: Shipments, **context: Any):
    shipment = await SqlShipmentRepository(session).get_for_order(order_id)
    if shipment is not None:
        return render(request, "delivery/_shipment.html", order_id=order_id, shipment=shipment, tones=TONES,
                      **context)  # fmt: skip
    form = await shipments.form(order_id)
    return render(request, "delivery/_shipment_form.html", order_id=order_id, form=form, **context)


@router.get("/orders/{order_id}/shipment")
async def shipment_section(request: Request, order_id: int, session: Session, shipments: Shipments) -> HTMLResponse:
    try:
        return await _shipment(request, order_id, session, shipments)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=str(error))


def _text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _seat(form: FormData) -> ShipmentSeat:
    """A ready box of the old site, or the typed weight and sizes."""
    preset = next((p for p in SEAT_PRESETS if p.name == _text(form, "box")), None)
    if preset is not None:
        return preset.seat
    return ShipmentSeat(
        weight=decimal_field(_text(form, "weight"), "Вага"),
        width=decimal_field(_text(form, "width"), "Ширина"),
        length=decimal_field(_text(form, "length"), "Довжина"),
        height=decimal_field(_text(form, "height"), "Висота"),
    )


@router.post("/orders/{order_id}/shipment")
async def create_shipment(
    request: Request, order_id: int, user: WebUser, session: Session, shipments: Shipments
) -> HTMLResponse:
    form = await request.form()
    try:
        create = CreateShipmentRequest(
            first_name=_text(form, "first_name"),
            last_name=_text(form, "last_name"),
            middle_name=_text(form, "middle_name"),
            phone=_text(form, "phone"),
            city_ref=_text(form, "city_ref"),
            warehouse_ref=_text(form, "warehouse_ref"),
            declared_cost=decimal_field(_text(form, "declared_cost"), "Оголошена вартість"),
            cash_on_delivery=decimal_field(_text(form, "cash_on_delivery"), "Накладений платіж"),
            sender_pays=form.get("sender_pays") == "1",
            seats=[_seat(form)],
            sender_id=_text(form, "sender_id") or None,
            dropoff_point_id=_text(form, "dropoff_point_id") or None,
        )
        await shipments.create(order_id, create, user.user_id)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    response = await _shipment(request, order_id, session, shipments, saved="ТТН створено.")
    response.headers["HX-Retarget"] = "#shipment"  # the form is replaced by the waybill
    return response


@router.post("/orders/{order_id}/shipment/delete")
async def delete_shipment(request: Request, order_id: int, session: Session, shipments: Shipments) -> HTMLResponse:
    try:
        await shipments.delete(order_id)
    except BUSINESS_ERRORS as error:
        return await _shipment(request, order_id, session, shipments, error=await undo(session, error))
    return await _shipment(request, order_id, session, shipments, saved="ТТН видалено.")


@router.get("/orders/{order_id}/shipment/print")
async def print_shipment(request: Request, order_id: int, shipments: Shipments, kind: str = "Document") -> Response:
    try:
        pdf, _ = await shipments.print(order_id, ShipmentPrintKind(kind))
    except (ValueError, *BUSINESS_ERRORS) as error:
        return render(request, "print_error.html", status_code=400, error=str(error))
    return Response(pdf, media_type="application/pdf")
