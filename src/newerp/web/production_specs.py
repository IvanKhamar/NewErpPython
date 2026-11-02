"""The editors of a production model: cut parts and both specifications (BOMs), the links to store products,
and the model stocktaking (ModelSpecifications, BomDialog, ModelLinks, ModelStocktakingDialog in Blazor).

Each page is one fragment that its forms redraw. A BOM form always has a few empty rows at the end for new lines;
a row without a consumable (or a part) is skipped, so no JavaScript is needed to add rows."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import FormData

from newerp.api.deps import Session
from newerp.api.production import ByWarehouse, Repository, Service
from newerp.api.security import Policies
from newerp.api.stock import ByWarehouse as StockPlaces
from newerp.api.stock import Repository as StockRepository
from newerp.application.production.models import (
    CategoryLinkRequest,
    ComponentBomRowRequest,
    ComponentCountRequest,
    ProductionStocktakingRequest,
    SaveComponentRequest,
    SetComponentBomRequest,
    SetProductionLinksRequest,
    SetSewnBomRequest,
    SewnBomRowRequest,
    SizeCountRequest,
    SizeOptionRequest,
)
from newerp.application.stock.ports import StockItemFilter
from newerp.domain.common import DomainError
from newerp.web.common import BUSINESS_ERRORS, WebUser, decimal_field, number, page_roles, render, undo

router = APIRouter(
    prefix="/app/production/models/{production_id}",
    include_in_schema=False,
    dependencies=[Depends(page_roles(*Policies.STOCK))],
)
EMPTY_ROWS = 3


def _column(form: FormData, name: str) -> list[str]:
    return [str(value).strip() for value in form.getlist(name)]


def _text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


# ── Cut parts and specifications ──


async def _specs(request: Request, production_id: int, repository: Repository, stock: StockRepository,
                 template: str = "production/_specs.html", **messages: Any) -> HTMLResponse:  # fmt: skip
    model = await repository.get_dto(production_id)
    if model is None:
        return render(request, "not_found.html", status_code=404)
    return render(request, template, model=model, items=await stock.list_items(StockItemFilter()),
                  empty_rows=range(EMPTY_ROWS), **messages)  # fmt: skip


@router.get("/specs")
async def specs_page(request: Request, production_id: int, repository: Repository, stock: StockRepository):
    return await _specs(request, production_id, repository, stock, "production/specs.html")


def _component_bom(form: FormData) -> SetComponentBomRequest:
    rows = zip(_column(form, "size_id"), _column(form, "item_id"), _column(form, "qty"), strict=True)
    return SetComponentBomRequest(
        rows=[
            ComponentBomRowRequest(
                size_id=number(size), item_id=number(item) or 0, qty_per_unit=decimal_field(amount, "Кількість")
            )  # fmt: skip
            for size, item, amount in rows
            if item
        ]
    )


def _sewn_bom(form: FormData) -> SetSewnBomRequest:
    """The "what" select holds "c:<part id>" for a cut part or "i:<item id>" for a consumable."""
    result = []
    for size, what, amount in zip(_column(form, "size_id"), _column(form, "what"), _column(form, "qty"), strict=True):
        kind, _, key = what.partition(":")
        if not key:
            continue
        result.append(
            SewnBomRowRequest(
                size_id=number(size),
                component_id=number(key) if kind == "c" else None,
                item_id=number(key) if kind == "i" else None,
                qty_per_unit=decimal_field(amount, "Кількість"),
            )
        )
    return SetSewnBomRequest(rows=result)


@router.post("/specs/{action}")
async def specs_action(
    request: Request,
    production_id: int,
    action: str,
    repository: Repository,
    stock: StockRepository,
    service: Service,
    session: Session,
) -> HTMLResponse:
    """Add, rename or delete a cut part, or save a part's cutting BOM or the model's sewing BOM."""
    form = await request.form()
    component_id = number(_text(form, "component_id"))
    try:
        if action == "add-component":
            await service.add_component(production_id, SaveComponentRequest(name=_text(form, "name"), sort_order=99))
            saved = "Деталь додано."
        elif action == "rename-component" and component_id:
            sort_order = number(_text(form, "sort_order")) or 0
            await service.update_component(component_id, SaveComponentRequest(name=_text(form, "name"),
                                                                               sort_order=sort_order))  # fmt: skip
            saved = "Деталь змінено."
        elif action == "delete-component" and component_id:
            await service.delete_component(component_id)
            saved = "Деталь видалено."
        elif action == "component-bom" and component_id:
            await service.set_component_bom(component_id, _component_bom(form))
            saved = "Специфікацію крою збережено."
        elif action == "sewn-bom":
            await service.set_sewn_bom(production_id, _sewn_bom(form))
            saved = "Специфікацію пошиву збережено."
        else:
            return render(request, "not_found.html", status_code=404)
    except BUSINESS_ERRORS as error:
        return await _specs(request, production_id, repository, stock, error=await undo(session, error))
    return await _specs(request, production_id, repository, stock, saved=saved)


# ── Links to store products ──


async def _links(request: Request, production_id: int, repository: Repository,
                 template: str = "production/_links.html", **messages: Any) -> HTMLResponse:  # fmt: skip
    model = await repository.get_dto(production_id)
    if model is None:
        return render(request, "not_found.html", status_code=404)
    return render(request, template, model=model, empty_rows=range(EMPTY_ROWS), **messages)


@router.get("/links")
async def links_page(request: Request, production_id: int, repository: Repository) -> HTMLResponse:
    return await _links(request, production_id, repository, "production/links.html")


def _links_request(form: FormData) -> SetProductionLinksRequest:
    """Product ids "101, 102"; category rows; each size's option value of the store's "Розмір" option."""
    product_ids = []
    for part in _text(form, "product_ids").replace(";", ",").split(","):
        if part.strip():
            if number(part.strip()) is None:
                raise DomainError(f"«{part.strip()}» — не номер товару.")
            product_ids.append(int(part.strip()))
    columns = (_column(form, "category_id"), _column(form, "name_filter"), _column(form, "match_exact"))
    categories = [
        CategoryLinkRequest(category_id=number(category) or 0, name_filter=name_filter, match_exact=exact == "1")
        for category, name_filter, exact in zip(*columns, strict=True)
        if category
    ]
    options = [
        SizeOptionRequest(size_id=number(size) or 0, option_value_id=number(option))
        for size, option in zip(_column(form, "size_id"), _column(form, "option_value_id"), strict=True)
    ]
    return SetProductionLinksRequest(product_ids=product_ids, categories=categories, size_options=options)


@router.post("/links")
async def save_links(
    request: Request, production_id: int, repository: Repository, service: Service, session: Session
) -> HTMLResponse:
    form = await request.form()
    try:
        await service.set_links(production_id, _links_request(form))
    except BUSINESS_ERRORS as error:
        return await _links(request, production_id, repository, error=await undo(session, error))
    return await _links(request, production_id, repository, saved="Прив'язки збережено, залишки передано на вітрину.")


# ── Model stocktaking ──


async def _stocktaking(request: Request, production_id: int, repository: Repository, places: ByWarehouse,
                       stock_places: StockPlaces, template: str = "production/_stocktaking.html",
                       **context: Any) -> HTMLResponse:  # fmt: skip
    model = await repository.get_dto(production_id)
    if model is None:
        return render(request, "not_found.html", status_code=404)
    return render(request, template, model=model, places=await places.get(production_id),
                  warehouses=await stock_places.warehouses(), **context)  # fmt: skip


@router.get("/stocktaking")
async def stocktaking_page(
    request: Request, production_id: int, repository: Repository, places: ByWarehouse, stock_places: StockPlaces
) -> HTMLResponse:
    return await _stocktaking(request, production_id, repository, places, stock_places, "production/stocktaking.html")


def _count(value: str, field: str):
    """An empty field was not counted (None); 0 is a count."""
    return decimal_field(value, field) if value else None


@router.post("/stocktaking")
async def recount(
    request: Request,
    production_id: int,
    user: WebUser,
    repository: Repository,
    places: ByWarehouse,
    stock_places: StockPlaces,
    service: Service,
    session: Session,
) -> HTMLResponse:
    form = await request.form()
    try:
        sizes = [
            SizeCountRequest(size_id=number(size) or 0, cut=_count(cut, "Крій"), sewn=_count(sewn, "Пошите"))
            for size, cut, sewn in zip(
                _column(form, "size_id"), _column(form, "cut"), _column(form, "sewn"), strict=True
            )  # fmt: skip
            if cut or sewn
        ]
        parts = zip(_column(form, "part_component_id"), _column(form, "part_size_id"), _column(form, "part_actual"),
                    strict=True)  # fmt: skip
        components = [
            ComponentCountRequest(
                component_id=number(component) or 0, size_id=number(size) or 0, actual=decimal_field(actual, "Деталі")
            )  # fmt: skip
            for component, size, actual in parts
            if actual
        ]
        recount_request = ProductionStocktakingRequest(
            sizes=sizes,
            components=components,
            comment=_text(form, "comment"),
            warehouse_id=number(_text(form, "warehouse_id")),
        )
        result = await service.recount(production_id, recount_request, user.user_id, user.username)
    except BUSINESS_ERRORS as error:
        return await _stocktaking(request, production_id, repository, places, stock_places,
                                  error=await undo(session, error))  # fmt: skip
    return await _stocktaking(request, production_id, repository, places, stock_places, result=result)
