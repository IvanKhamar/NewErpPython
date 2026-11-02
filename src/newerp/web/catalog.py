"""Catalog pages: products and their card, the product sort by sales with pins and snapshots, pre-orders
(Products.razor, ProductCard.razor, ProductSort.razor, PreOrders.razor)."""

import math
from datetime import date, datetime
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from newerp.api.catalog import Products, Sorting
from newerp.api.deps import Session
from newerp.api.pre_orders import Service as PreOrders
from newerp.api.security import Policies
from newerp.application.catalog.pre_orders import PreOrderFilter, UpdatePreOrderRequest
from newerp.application.catalog.products import ProductFilter, UpdateProductRequest
from newerp.application.catalog.sort import (
    CrossSellMetricDto,
    RecalculateGroupRequest,
    SalesPeriod,
    SetPinRequest,
    SortGroupDto,
)
from newerp.domain.catalog import SizeAvailability, SortGroupType
from newerp.domain.common import DomainError
from newerp.web.common import BUSINESS_ERRORS, decimal_field, is_fragment, number, page_roles, qty, render, undo

router = APIRouter(prefix="/app", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))])
Field = Annotated[str, Form()]

PAGE_SIZE = 20
AVAILABILITY = {
    SizeAvailability.IN_STOCK: ("Є на складі", "received"),
    SizeAvailability.PRE_ORDER: ("Передзамовлення", "ordered"),
    SizeAvailability.SOLD_OUT: ("Немає", "none"),
}


# ── Products ──


@router.get("/catalog/products")
async def product_list(
    request: Request,
    products: Products,
    search: str = "",
    category: str = "",
    label: str = "",
    enabled: str = "",
    page: str = "1",
) -> HTMLResponse:
    current = max(number(page) or 1, 1)
    product_filter = ProductFilter(
        search=search.strip() or None,
        category_id=number(category),
        label_id=number(label),
        enabled={"yes": True, "no": False}.get(enabled),
        page=current,
        page_size=PAGE_SIZE,
    )
    result = await products.list_page(product_filter)
    pages = max(1, math.ceil(result.total_count / PAGE_SIZE))

    def link(to_page: int) -> str:
        fields = {"search": search, "category": category, "label": label, "enabled": enabled, "page": to_page}
        return urlencode({key: value for key, value in fields.items() if value})

    context = {
        "result": result,
        "pages": pages,
        "previous_page": link(current - 1) if current > 1 else None,
        "next_page": link(current + 1) if current < pages else None,
    }
    if is_fragment(request):
        return render(request, "catalog/_products.html", **context)
    values = {"search": search, "category": number(category), "label": number(label), "enabled": enabled}
    return render(request, "catalog/products.html", values=values, categories=await products.categories(),
                  labels=await products.labels(), **context)  # fmt: skip


async def _product(request: Request, product_id: int, products: Products, template: str, **messages: Any):
    try:
        product = await products.get(product_id)
    except BUSINESS_ERRORS:
        return render(request, "not_found.html", status_code=404)
    return render(request, template, product=product, labels=await products.labels(),
                  categories=await products.categories(), **messages)  # fmt: skip


@router.get("/catalog/products/{product_id}")
async def product_card(request: Request, product_id: int, products: Products) -> HTMLResponse:
    return await _product(request, product_id, products, "catalog/product.html")


@router.post("/catalog/products/{product_id}")
async def update_product(request: Request, product_id: int, products: Products, session: Session) -> HTMLResponse:
    """Price, on/off, labels and categories; the card's date_modified catches a change made meanwhile (409)."""
    form = await request.form()
    try:
        update = UpdateProductRequest(
            price=decimal_field(str(form.get("price", "")), "Ціна"),
            enabled=form.get("enabled") == "1",
            label_ids=[number(str(v)) or 0 for v in form.getlist("label_id")],
            category_ids=[number(str(v)) or 0 for v in form.getlist("category_id")],
            date_modified=datetime.fromisoformat(str(form.get("date_modified", ""))),
        )
        await products.update(product_id, update)
    except ValueError:
        return await _product(request, product_id, products, "catalog/_product.html", error="Оновіть сторінку.")
    except BUSINESS_ERRORS as error:
        error_text = await undo(session, error)
        return await _product(request, product_id, products, "catalog/_product.html", error=error_text)
    return await _product(request, product_id, products, "catalog/_product.html", saved="Зміни збережено.")


# ── Product sort ──


def _period(days: str) -> SalesPeriod:
    return SalesPeriod(days=number(days) or 90)


async def _sort_page(request: Request, sorting: Sorting, template: str, group: str = "", days: str = "90",
                     **context: Any) -> HTMLResponse:  # fmt: skip
    groups = await sorting.list_groups()
    chosen = next((g for g in groups if _group_value(g) == group), groups[0] if groups else None)
    preview = await sorting.preview(chosen.type, chosen.key, _period(days)) if chosen else []
    return render(
        request,
        template,
        groups=groups,
        chosen=chosen,
        group_value=_group_value,
        preview=preview,
        days=days,
        pins=await sorting.list_pins(),
        snapshots=(await sorting.list_snapshots())[:10],
        metric=await sorting.get_cross_sell_metric(),
        **context,
    )


def _group_value(group: SortGroupDto) -> str:
    """A group in one select value: "type:key"."""
    return f"{int(group.type)}:{group.key}"


def _parse_group(value: str) -> tuple[SortGroupType, str]:
    type_, _, key = value.partition(":")
    try:
        return SortGroupType(int(type_)), key
    except ValueError:
        raise DomainError("Оберіть групу товарів.") from None


@router.get("/catalog/sort")
async def sort_page(request: Request, sorting: Sorting, group: str = "", days: str = "90") -> HTMLResponse:
    template = "catalog/_sort.html" if is_fragment(request) else "catalog/sort.html"
    return await _sort_page(request, sorting, template, group, days)


@router.post("/catalog/sort/{action}")
async def sort_action(request: Request, action: str, sorting: Sorting, session: Session) -> HTMLResponse:
    """Write the new order of the group or of all groups, pin or unpin, restore a snapshot, the cross-sell metric."""
    form = await request.form()
    group, days = str(form.get("group", "")), str(form.get("days", "90"))
    try:
        if action == "recalculate":
            type_, key = _parse_group(group)
            result = await sorting.recalculate_group(RecalculateGroupRequest(type=type_, key=key, period=_period(days)))
            saved = f"Порядок записано: змінено позицій {result.updated}."
        elif action == "recalculate-all":
            result = await sorting.recalculate_all(_period(days))
            saved = f"Усі групи перераховано: змінено позицій {result.updated}."
        elif action == "pin":
            type_, key = _parse_group(group)
            position, product_id = number(str(form.get("position", ""))), number(str(form.get("product_id", "")))
            if not position or not product_id:
                raise DomainError("Вкажіть позицію й номер товару.")
            await sorting.set_pin(SetPinRequest(type=type_, key=key, position=position, product_id=product_id))
            saved = "Товар закріплено."
        elif action == "unpin":
            await sorting.remove_pin(number(str(form.get("pin_id", ""))) or 0)
            saved = "Закріплення знято."
        elif action == "restore":
            await sorting.restore_snapshot(number(str(form.get("snapshot_id", ""))) or 0)
            saved = "Порядок повернуто зі знімка."
        elif action == "metric":
            await sorting.set_cross_sell_metric(CrossSellMetricDto(panel_only=form.get("panel_only") == "1"))
            saved = "Збережено."
        else:
            return render(request, "not_found.html", status_code=404)
    except BUSINESS_ERRORS as error:
        error_text = await undo(session, error)
        return await _sort_page(request, sorting, "catalog/_sort.html", group, days, error=error_text)
    return await _sort_page(request, sorting, "catalog/_sort.html", group, days, saved=saved)


# ── Pre-orders ──


@router.get("/preorders")
async def pre_order_page(request: Request, pre_orders: PreOrders, search: str = "", quota: str = "") -> HTMLResponse:
    rows = await pre_orders.list_rows(PreOrderFilter(search=search.strip() or None, only_with_quota=bool(quota)))
    template = "catalog/_pre_orders.html" if is_fragment(request) else "catalog/pre_orders.html"
    return render(request, template, rows=rows, availability=AVAILABILITY, search=search, quota=bool(quota))


@router.post("/preorders/settle")
async def settle(request: Request, pre_orders: PreOrders) -> HTMLResponse:
    result = await pre_orders.settle()
    return render(request, "_note.html", text=f"Списано продане наперед: розмірів {result.sizes}.")


@router.post("/preorders/{option_value_id}")
async def update_pre_order(
    request: Request, option_value_id: int, pre_orders: PreOrders, session: Session, quota: Field = "",
    date_available: Field = "",
) -> HTMLResponse:  # fmt: skip
    """One size row: the pre-order limit and the date it is expected; the answer is the message next to it."""
    try:
        limit = decimal_field(quota, "Ліміт")
        if limit != limit.to_integral_value():
            raise DomainError("Ліміт: потрібне ціле число.")
        expected = date.fromisoformat(date_available) if date_available else None
        await pre_orders.update(option_value_id, UpdatePreOrderRequest(quota=int(limit), date_available=expected))
    except ValueError:
        return render(request, "_error.html", error="Дата: потрібен формат 2026-11-30.")
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    return render(request, "_note.html", text=f"Збережено: ліміт {qty(limit)}.")
