"""Production pages: models and their card with the operations, sewn pieces by warehouse, sewing plan, log
(Pages/Manufacturing). Specifications, product links and the model stocktaking are edited through the API for now."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import FormData

from newerp.api.deps import Session
from newerp.api.production import ByWarehouse, Repository, Service, get_plan_service
from newerp.api.security import Policies
from newerp.api.stock import ByWarehouse as StockPlaces
from newerp.application.common import NotFoundError
from newerp.application.production.models import (
    ProductionLogFilter,
    ProductionModelDto,
    ProductionOperation,
    ProductionOperationRequest,
    SaveProductionModelRequest,
    SizeQuantity,
    SizeRequest,
    TransferSewnRequest,
)
from newerp.application.production.ports import ProductionModelFilter
from newerp.application.production.services import SewingPlanService
from newerp.domain.common import DomainError
from newerp.web.common import (
    BUSINESS_ERRORS,
    Redirect,
    WebUser,
    decimal_field,
    is_fragment,
    number,
    page_roles,
    qty,
    render,
    undo,
)

router = APIRouter(
    prefix="/app/production", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.STOCK))]
)
Plan = Annotated[SewingPlanService, Depends(get_plan_service)]

TYPES = {"clothing": "Власний пошив", "ready_made": "Готовий товар"}
STAGES = {"cut": "Крій", "sewn": "Пошите"}
LOG_LABELS = {
    ("cut", "in"): "Покроєно",
    ("cut", "out"): "В пошив",
    ("sewn", "in"): "Пошито / прихід",
    ("sewn", "out"): "Відвантажено / замовлення",
}


def operations(model_type: str) -> list[tuple[str, str]]:
    """The operation buttons; ready goods are not cut or sewn, "sewn" is a receipt from the supplier (ProductionUi)."""
    if model_type == "ready_made":
        return [(ProductionOperation.SEWN, "Прихід"), (ProductionOperation.SHIP, "Відвантажено")]
    return [
        (ProductionOperation.CUT, "Покроєно"),
        (ProductionOperation.SEND_TO_SEWING, "В пошив"),
        (ProductionOperation.SEWN, "Пошито"),
        (ProductionOperation.SHIP, "Відвантажено"),
    ]


def _text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _column(form: FormData, name: str) -> list[str]:
    return [str(value) for value in form.getlist(name)]


# ── Models ──


@router.get("")
async def model_list(request: Request, repository: Repository, type: str = "", low: str = "") -> HTMLResponse:
    model_filter = ProductionModelFilter(type if type in TYPES else None, below_minimum_only=bool(low))
    models = await repository.list_models(model_filter)
    if is_fragment(request):
        return render(request, "production/_models.html", models=models, types=TYPES)
    return render(request, "production/models.html", models=models, types=TYPES, type=type, low=bool(low))


async def _model_form(
    request: Request, values: dict[str, Any], production_id: int | None = None, error: str | None = None
) -> HTMLResponse:
    sizes = list(values.get("sizes", []))
    sizes += [("", "")] * 3  # empty rows for new sizes; empty names are skipped
    return render(
        request,
        "production/model_form.html",
        status_code=200 if error is None else 400,
        values=values,
        sizes=sizes,
        production_id=production_id,
        types=TYPES,
        error=error,
    )


def _model_values(model: ProductionModelDto) -> dict[str, Any]:
    return {
        "name": model.name,
        "type": model.type,
        "min_total": str(model.min_total),
        "sort_order": str(model.sort_order),
        "sizes": [(size.name, qty(size.percent)) for size in model.sizes],
    }


def _form_values(form: FormData) -> dict[str, Any]:
    sizes = zip(_column(form, "size_name"), _column(form, "size_percent"), strict=True)
    return {name: _text(form, name) for name in ("name", "type", "min_total", "sort_order")} | {
        "sizes": [(name, percent) for name, percent in sizes if name.strip()]
    }


def _whole(value: str, field: str) -> int:
    amount = decimal_field(value, field)
    if amount != amount.to_integral_value():
        raise DomainError(f"{field}: потрібне ціле число.")
    return int(amount)


def _model_request(values: dict[str, Any]) -> SaveProductionModelRequest:
    return SaveProductionModelRequest(
        name=values["name"],
        type=values["type"],
        min_total=_whole(values["min_total"], "Мінімум пошитих"),
        sort_order=_whole(values["sort_order"], "Порядок"),
        sizes=[
            SizeRequest(name=name.strip(), percent=decimal_field(percent, "Частка, %"))
            for name, percent in values["sizes"]
        ],
    )


@router.get("/models/new")
async def new_model_page(request: Request) -> HTMLResponse:
    return await _model_form(request, {"type": "clothing", "min_total": "50", "sort_order": "0"})


@router.post("/models/new")
async def create_model(request: Request, service: Service, session: Session) -> HTMLResponse:
    values = _form_values(await request.form())
    try:
        production_id = await service.create_model(_model_request(values))
    except BUSINESS_ERRORS as error:
        return await _model_form(request, values, error=await undo(session, error))
    raise Redirect(f"/app/production/models/{production_id}")


@router.get("/models/{production_id}/edit")
async def edit_model_page(request: Request, production_id: int, repository: Repository) -> HTMLResponse:
    model = await repository.get_dto(production_id)
    if model is None:
        return render(request, "not_found.html", status_code=404)
    return await _model_form(request, _model_values(model), production_id)


@router.post("/models/{production_id}/edit")
async def update_model(request: Request, production_id: int, service: Service, session: Session) -> HTMLResponse:
    values = _form_values(await request.form())
    try:
        await service.update_model(production_id, _model_request(values))
    except BUSINESS_ERRORS as error:
        return await _model_form(request, values, production_id, await undo(session, error))
    raise Redirect(f"/app/production/models/{production_id}")


@router.post("/models/{production_id}/deactivate")
async def deactivate_model(request: Request, production_id: int, service: Service) -> HTMLResponse:
    try:
        await service.deactivate_model(production_id)
    except NotFoundError:
        return render(request, "not_found.html", status_code=404)
    raise Redirect("/app/production")


async def _model_card(
    request: Request,
    production_id: int,
    repository: Repository,
    places: ByWarehouse,
    stock_places: StockPlaces,
    template: str = "production/_model.html",
    **messages: Any,
) -> HTMLResponse:
    model = await repository.get_dto(production_id)
    if model is None:
        return render(request, "not_found.html", status_code=404)
    return render(
        request,
        template,
        model=model,
        type_name=TYPES.get(model.type, model.type),
        operations=operations(model.type),
        places=await places.get(production_id),
        transfers=await places.transfers(production_id),
        warehouses=await stock_places.warehouses(),
        log=await repository.list_log(ProductionLogFilter(production_id, limit=20)),
        log_labels=LOG_LABELS,
        **messages,
    )


@router.get("/models/{production_id}")
async def model_card(
    request: Request, production_id: int, repository: Repository, places: ByWarehouse, stock_places: StockPlaces
) -> HTMLResponse:
    return await _model_card(request, production_id, repository, places, stock_places, "production/model.html")


@router.post("/models/{production_id}/operations")
async def record_operation(
    request: Request,
    production_id: int,
    user: WebUser,
    repository: Repository,
    places: ByWarehouse,
    stock_places: StockPlaces,
    service: Service,
    session: Session,
) -> HTMLResponse:
    """One of the four buttons with quantities for several sizes at once (OperationPanel.razor)."""
    form = await request.form()
    try:
        operation = ProductionOperation(_text(form, "operation"))
    except ValueError:
        return render(request, "not_found.html", status_code=404)
    try:
        quantities = [
            SizeQuantity(size_id=number(size_id) or 0, quantity=decimal_field(quantity, "Кількість"))
            for size_id, quantity in zip(_column(form, "size_id"), _column(form, "quantity"), strict=True)
            if quantity.strip() not in ("", "0")
        ]
        record = ProductionOperationRequest(
            quantities=quantities, comment=_text(form, "comment"), warehouse_id=number(_text(form, "warehouse_id"))
        )
        result = await service.record(production_id, operation, record, user.user_id, user.username)
    except BUSINESS_ERRORS as error:
        return await _model_card(
            request, production_id, repository, places, stock_places, error=await undo(session, error)
        )
    return await _model_card(
        request, production_id, repository, places, stock_places, result=result, saved="Записано в журнал."
    )


@router.post("/models/{production_id}/transfers")
async def transfer(
    request: Request,
    production_id: int,
    user: WebUser,
    repository: Repository,
    places: ByWarehouse,
    stock_places: StockPlaces,
    session: Session,
) -> HTMLResponse:
    form = await request.form()
    try:
        move = TransferSewnRequest(
            size_id=number(_text(form, "size_id")) or 0,
            from_warehouse_id=number(_text(form, "from_warehouse_id")),
            to_warehouse_id=number(_text(form, "to_warehouse_id")),
            quantity=decimal_field(_text(form, "quantity"), "Кількість"),
            comment=_text(form, "comment"),
        )
        await places.transfer(production_id, move, user.user_id, user.username)
    except BUSINESS_ERRORS as error:
        return await _model_card(
            request, production_id, repository, places, stock_places, error=await undo(session, error)
        )
    return await _model_card(request, production_id, repository, places, stock_places, saved="Переміщено.")


# ── Reports ──


@router.get("/by-warehouse")
async def by_warehouse(request: Request, places: ByWarehouse) -> HTMLResponse:
    return render(request, "production/by_warehouse.html", report=await places.by_warehouse())


@router.get("/plan")
async def sewing_plan(request: Request, plan: Plan, sizes: str = "") -> HTMLResponse:
    show_all = sizes == "all"  # also the sizes that are in stock
    return render(request, "production/plan.html", plan=await plan.get(only_to_sew=not show_all), show_all=show_all)


@router.get("/log")
async def production_log(request: Request, repository: Repository, stage: str = "") -> HTMLResponse:
    entries = await repository.list_log(ProductionLogFilter(stage=stage if stage in STAGES else None, limit=200))
    return render(request, "production/log.html", log=entries, log_labels=LOG_LABELS, stage=stage, stages=STAGES)
