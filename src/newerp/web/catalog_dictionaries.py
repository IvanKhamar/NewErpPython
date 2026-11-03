"""Catalog dictionary pages (Python first): labels and search presets on one page, categories with their form."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from starlette.datastructures import FormData

from newerp.api.catalog import Dictionaries
from newerp.api.deps import Session
from newerp.api.security import Policies
from newerp.application.catalog.dictionaries import CategoryFormDto, LabelDto, SearchPresetDto
from newerp.web.common import BUSINESS_ERRORS, WebUser, is_fragment, number, page_roles, render, undo

router = APIRouter(prefix="/app/catalog", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))])
Field = Annotated[str, Form()]
admin_only = [Depends(page_roles(*Policies.CATALOG_BULK))]


def _text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _invalid(error: ValidationError) -> str:
    return "Перевірте поля: " + ", ".join(str(item["loc"][0]) for item in error.errors()) + "."


# ── Labels and search presets ──


async def _lists(request: Request, service: Dictionaries, **messages: Any) -> HTMLResponse:
    template = "catalog/_dictionaries.html" if is_fragment(request) else "catalog/dictionaries.html"
    return render(request, template, labels=await service.labels(), presets=await service.presets(), **messages)


@router.get("/labels")
async def dictionaries_page(request: Request, service: Dictionaries) -> HTMLResponse:
    return await _lists(request, service)


@router.post("/labels/{kind}/{item_id}/{action}")
async def change_dictionary(
    request: Request,
    kind: Literal["label", "preset"],
    item_id: int,
    action: Literal["save", "delete"],
    user: WebUser,
    service: Dictionaries,
    session: Session,
) -> HTMLResponse:
    """item_id 0 with save creates a new one."""
    form = await request.form()
    try:
        if kind == "label" and action == "save":
            label = LabelDto(
                name=_text(form, "name"), background_color=_text(form, "background_color"),
                text_color=_text(form, "text_color"), sort_order=number(_text(form, "sort_order")) or 0,
                enabled=_text(form, "enabled") == "1",
            )  # fmt: skip
            await service.save_label(item_id or None, label)
        elif kind == "label":
            await service.delete_label(item_id, user.user_id)
        elif action == "save":
            preset = SearchPresetDto(
                name=_text(form, "name"), url=_text(form, "url"), sort_order=number(_text(form, "sort_order")) or 0,
                enabled=_text(form, "enabled") == "1",
            )  # fmt: skip
            await service.save_preset(item_id or None, preset)
        else:
            await service.delete_preset(item_id)
    except ValidationError as error:
        return await _lists(request, service, error=_invalid(error))
    except BUSINESS_ERRORS as error:
        return await _lists(request, service, error=await undo(session, error))
    return await _lists(request, service, saved="Збережено.")


# ── Categories ──


@router.get("/categories")
async def categories_page(request: Request, service: Dictionaries) -> HTMLResponse:
    return render(
        request, "catalog/categories.html", categories=await service.categories(), bulk_roles=Policies.CATALOG_BULK
    )


@router.post("/categories/repair", dependencies=admin_only)
async def repair(request: Request, service: Dictionaries) -> HTMLResponse:
    return render(request, "_note.html", text=f"Дерево перебудовано: категорій {await service.repair_paths()}.")


def _category_request(form: FormData) -> CategoryFormDto:
    texts = (
        "name product_name advertising_product_name description description_table_size meta_title meta_description "
        "meta_keyword seo_keyword image image_table_size"
    ).split()
    return CategoryFormDto(
        **{name: _text(form, name) for name in texts},
        parent_id=number(_text(form, "parent_id")) or 0,
        column=number(_text(form, "column")) or 0,
        sort_order=number(_text(form, "sort_order")) or 0,
        production_period=number(_text(form, "production_period")) or 0,
        banner_id=number(_text(form, "banner_id")) or 0,
        top=_text(form, "top") == "1",
        enabled=_text(form, "enabled") == "1",
        filter_ids=[number(str(v)) or 0 for v in form.getlist("filter_ids")],
    )


async def _category_page(
    request: Request, service: Dictionaries, category: CategoryFormDto, category_id: int | None, **messages: Any
) -> HTMLResponse:
    template = "catalog/_category_form.html" if is_fragment(request) else "catalog/category.html"
    others = [c for c in await service.categories() if c.id != category_id]
    return render(request, template, category=category, category_id=category_id, parents=others,
                  filters=await service.filters(), bulk_roles=Policies.CATALOG_BULK, **messages)  # fmt: skip


@router.get("/categories/new")
async def new_category(request: Request, service: Dictionaries) -> HTMLResponse:
    return await _category_page(request, service, CategoryFormDto(), None)


@router.get("/categories/{category_id}")
async def edit_category(request: Request, category_id: int, service: Dictionaries) -> HTMLResponse:
    try:
        category = await service.category(category_id)
    except BUSINESS_ERRORS:
        return render(request, "not_found.html", status_code=404)
    return await _category_page(request, service, category, category_id)


@router.post("/categories/{category_id}")
async def save_category(
    request: Request, category_id: int, user: WebUser, service: Dictionaries, session: Session
) -> HTMLResponse:
    """category_id 0 creates a new one; the form comes back with the saved data or the error and what was typed."""
    form = await request.form()
    try:
        typed = _category_request(form)
    except ValidationError as error:
        return render(request, "_error.html", error=_invalid(error))
    try:
        saved = await service.save_category(category_id or None, typed, user.user_id)
    except BUSINESS_ERRORS as error:
        return await _category_page(request, service, typed, category_id or None, error=await undo(session, error))
    if not category_id:
        return Response(status_code=204, headers={"HX-Redirect": f"/app/catalog/categories/{saved.id}"})
    return await _category_page(request, service, saved, saved.id, saved_text="Категорію збережено.")


@router.post("/categories/{category_id}/delete", dependencies=admin_only)
async def delete_category(
    request: Request, category_id: int, user: WebUser, service: Dictionaries, session: Session
) -> Response:
    try:
        await service.delete_category(category_id, user.user_id)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    return Response(status_code=204, headers={"HX-Redirect": "/app/catalog/categories"})
