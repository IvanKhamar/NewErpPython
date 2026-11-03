"""Catalog pages: products and their card, the product sort by sales with pins and snapshots, pre-orders
(Products.razor, ProductCard.razor, ProductSort.razor, PreOrders.razor)."""

import math
from datetime import date, datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile as StarletteUpload

from newerp.api.catalog import Editor, Imports, Prices, Products, Sorting
from newerp.api.deps import Session
from newerp.api.pre_orders import Service as PreOrders
from newerp.api.security import Policies
from newerp.application.catalog.pre_orders import PreOrderFilter, UpdatePreOrderRequest
from newerp.application.catalog.prices import MAX_IMPORT_BYTES, PriceChangeRequest
from newerp.application.catalog.product_edit import (
    ProductAttributeDto,
    ProductFormDto,
    ProductImageDto,
    ProductSizeEditDto,
    ProductSpecialDto,
    RemoveNewRequest,
    SaveProductRequest,
)
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
from newerp.web.common import (
    BUSINESS_ERRORS,
    WebUser,
    decimal_field,
    is_fragment,
    number,
    page_roles,
    qty,
    render,
    undo,
)

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


# ── The whole product form (Python first; before /{product_id}, or "new" would be read as a number) ──

TEXT_FIELDS = (
    "model sku name description tag meta_title meta_description meta_keyword table_size variant notice model_info "
    "characteristics color seo_keyword image"
).split()
NUMBER_FIELDS = ("price", "weight", "length", "width", "height")
INT_FIELDS = ("stock_status_id", "manufacturer_id", "sort_order")
LINK_FIELDS = ("related_ids", "related_color_ids", "related_variant_ids")
SPECIAL_ROWS_EMPTY = 2  # empty rows for new sale prices under the saved ones
SPECIAL_COLUMNS = ("group", "priority", "price", "percent", "start", "end")


def _ids(text: str) -> list[int]:
    """ "12, 15 40" → [12, 15, 40]; anything else is refused, not skipped."""
    parts = [p for p in text.replace(",", " ").split() if p]
    if any(not (p.isascii() and p.isdigit()) for p in parts):
        raise DomainError(f"Номери товарів через кому, наприклад 12, 15: «{text}».")
    return [int(p) for p in parts]


def _day(text: str) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError as error:
        raise DomainError(f"Дата має бути у форматі РРРР-ММ-ДД: «{text}».") from error


def _product_request(form: FormData) -> SaveProductRequest:
    """The form fields as the request the API takes as JSON; repeated fields are lists and table rows."""

    def text(name: str) -> str:
        value = form.get(name, "")
        return value if isinstance(value, str) else ""

    specials = [
        ProductSpecialDto(
            customer_group_id=number(group) or 1,
            priority=number(priority) or 1,
            price=decimal_field(price, "Акційна ціна"),
            percent=decimal_field(percent, "Знижка, %"),
            date_start=_day(start),
            date_end=_day(end),
        )
        for group, priority, price, percent, start, end in zip(
            *(map(str, form.getlist(f"special_{n}")) for n in SPECIAL_COLUMNS), strict=True
        )
        if price.strip() or percent.strip()
    ]
    sizes = [
        ProductSizeEditDto(option_value_id=int(v), price=decimal_field(text(f"size_price_{v}"), "Ціна розміру"))
        for v in map(str, form.getlist("size"))
        if v.isdigit()
    ]
    images = [ProductImageDto(image=line.strip(), sort_order=n) for n, line in enumerate(text("images").splitlines())]
    modified = text("date_modified")
    return SaveProductRequest(
        **{name: text(name) for name in TEXT_FIELDS},
        **{name: decimal_field(text(name), name) for name in NUMBER_FIELDS},
        **{name: number(text(name).strip()) or 0 for name in INT_FIELDS},
        **{name: _ids(text(name)) for name in LINK_FIELDS},
        minimum=number(text("minimum").strip()) or 1,
        subtract=text("subtract") == "1",
        enabled=text("enabled") == "1",
        category_ids=[number(str(v)) or 0 for v in form.getlist("category_ids")],
        label_ids=[number(str(v)) or 0 for v in form.getlist("label_ids")],
        filter_ids=[number(str(v)) or 0 for v in form.getlist("filter_ids")],
        attributes=[
            ProductAttributeDto(attribute_id=int(key[5:]), text=value)
            for key, value in form.multi_items()
            if key.startswith("attr_") and key[5:].isdigit() and isinstance(value, str) and value.strip()
        ],
        download_ids=[number(str(v)) or 0 for v in form.getlist("download_ids")],
        images=[i for i in images if i.image],
        specials=specials,
        sizes=sizes,
        date_modified=datetime.fromisoformat(modified) if modified else None,
    )


def _invalid(error: ValidationError) -> str:
    names = ", ".join(str(item["loc"][0]) for item in error.errors())
    return f"Перевірте поля: {names}."


async def _form_page(
    request: Request, editor: Editor, product: ProductFormDto, product_id: int | None, **messages: Any
) -> HTMLResponse:
    template = "catalog/_product_form.html" if is_fragment(request) else "catalog/product_form.html"
    return render(request, template, product=product, product_id=product_id, lookups=await editor.lookups(),
                  empty_specials=SPECIAL_ROWS_EMPTY, **messages)  # fmt: skip


@router.get("/catalog/products/new")
async def new_product_page(request: Request, editor: Editor) -> HTMLResponse:
    return await _form_page(request, editor, ProductFormDto(), None)


@router.post("/catalog/products/new")
async def create_product(request: Request, user: WebUser, editor: Editor, session: Session) -> Response:
    form = await request.form()
    try:
        created = await editor.create(_product_request(form), user.user_id)
    except ValidationError as error:
        return render(request, "_error.html", error=_invalid(error))
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    return Response(status_code=204, headers={"HX-Redirect": f"/app/catalog/products/{created.product_id}/edit"})


@router.post("/catalog/products/remove-new")
async def remove_new(request: Request, editor: Editor, session: Session, sku: Field = "") -> HTMLResponse:
    try:
        result = await editor.remove_new(RemoveNewRequest(sku=sku[:64]))
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    return render(request, "_note.html", text=f"NEW знято з товарів: {', '.join(map(str, result.product_ids))}.")


@router.get("/catalog/products/{product_id}/edit")
async def edit_product_page(request: Request, product_id: int, editor: Editor) -> HTMLResponse:
    try:
        product = await editor.get(product_id)
    except BUSINESS_ERRORS:
        return render(request, "not_found.html", status_code=404)
    return await _form_page(request, editor, product, product_id)


@router.post("/catalog/products/{product_id}/edit")
async def save_product(
    request: Request, product_id: int, user: WebUser, editor: Editor, session: Session
) -> HTMLResponse:
    """The form is shown again: saved with its new version, or with the error and what was typed."""
    form = await request.form()
    typed: ProductFormDto | None = None
    try:
        save = _product_request(form)
        typed = save
        saved = await editor.save(product_id, save, user.user_id)
    except ValidationError as error:
        return render(request, "_error.html", error=_invalid(error))
    except BUSINESS_ERRORS as error:
        message = await undo(session, error)
        if typed is None:
            return render(request, "_error.html", error=message)
        response = await _form_page(request, editor, typed, product_id, error=message)
        response.headers["HX-Retarget"] = "#product-form"
        return response
    response = await _form_page(request, editor, saved, product_id, saved="Товар збережено.")
    response.headers["HX-Retarget"] = "#product-form"
    return response


@router.post("/catalog/products/{product_id}/copy")
async def copy_product(request: Request, product_id: int, user: WebUser, editor: Editor, session: Session) -> Response:
    try:
        created = await editor.copy(product_id, user.user_id)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    return Response(status_code=204, headers={"HX-Redirect": f"/app/catalog/products/{created.product_id}/edit"})


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


# ── Quick price edit in the list, mass prices and the CSV import (Python first) ──


@router.post("/catalog/products/{product_id}/price")
async def quick_price(
    request: Request,
    product_id: int,
    products: Products,
    session: Session,
    price: Field = "",
    date_modified: Field = "",
) -> HTMLResponse:
    """The price cell of the list: saved with the row's date_modified, so a change made meanwhile is refused."""
    error = None
    try:
        update = UpdateProductRequest(
            price=decimal_field(price, "Ціна"), date_modified=datetime.fromisoformat(date_modified)
        )
        await products.update(product_id, update)
    except ValueError:
        error = "Оновіть сторінку."
    except BUSINESS_ERRORS as exc:
        error = await undo(session, exc)
    card = await products.get(product_id)
    return render(request, "catalog/_price_cell.html", product=card, error=error, saved=error is None)


bulk = [Depends(page_roles(*Policies.CATALOG_BULK))]


def _price_request(form: FormData) -> PriceChangeRequest:
    def text(name: str) -> str:
        value = form.get(name, "")
        return value.strip() if isinstance(value, str) else ""

    return PriceChangeRequest(
        target="sale" if text("target") == "sale" else "price",
        operation=text("operation") or "add",
        amount=decimal_field(text("amount"), "Число"),
        unit=text("unit") or "amount",
        category_ids=[number(str(v)) or 0 for v in form.getlist("category_ids")],
        all_products=text("all_products") == "1",
    )


@router.get("/catalog/prices", dependencies=bulk)
async def prices_page(request: Request, editor: Editor) -> HTMLResponse:
    return render(request, "catalog/prices.html", categories=(await editor.lookups()).categories)


@router.post("/catalog/prices/{action}", dependencies=bulk)
async def change_prices(
    request: Request, action: Literal["preview", "apply"], user: WebUser, prices: Prices, session: Session
) -> HTMLResponse:
    form = await request.form()
    try:
        price_request = _price_request(form)
        if action == "apply":
            result = await prices.apply(price_request, user.user_id)
            return render(request, "_note.html", text=f"Змінено товарів: {result.changed}.")
        preview = await prices.preview(price_request)
    except ValidationError as error:
        return render(request, "_error.html", error=_invalid(error))
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    return render(request, "catalog/_price_preview.html", preview=preview, target=price_request.target)


@router.post("/catalog/import/{action}", dependencies=bulk)
async def import_products(
    request: Request, action: Literal["preview", "apply"], user: WebUser, imports: Imports, session: Session
) -> HTMLResponse:
    form = await request.form()
    file = form.get("file")
    if not isinstance(file, StarletteUpload):
        return render(request, "_error.html", error="Оберіть CSV-файл.")
    content = await file.read(MAX_IMPORT_BYTES + 1)
    try:
        if action == "apply":
            result = await imports.apply(content, user.user_id)
            return render(
                request, "_note.html", text=f"Оновлено товарів: {result.updated}, з помилками: {result.errors}."
            )
        preview = await imports.preview(content)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    return render(request, "catalog/_import_preview.html", preview=preview)
