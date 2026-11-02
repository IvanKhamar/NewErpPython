"""Warehouse pages: consumables, stock by warehouse, stocktaking, reorder, purchase orders, suppliers."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import FormData, UploadFile

from newerp.api.deps import Session
from newerp.api.files import xlsx_file
from newerp.api.security import Policies
from newerp.api.stock import ByWarehouse, Orders, PurchaseOrders, Reorder, Repository, Stock, Stocktaking
from newerp.application.stock.models import (
    AdjustStockRequest,
    ApplyStocktakingRequest,
    CreatePurchaseOrderRequest,
    CreateStockCategoryRequest,
    CreateStockSupplierRequest,
    PurchaseOrderLineRequest,
    ReceiptLineRequest,
    ReceivePurchaseOrderRequest,
    SaveStockItemRequest,
    SetPurchaseOrderLinesRequest,
    StockItemDto,
    StocktakingCountRequest,
    TransferStockRequest,
)
from newerp.application.stock.ports import StockItemFilter
from newerp.application.stock.services import MAX_FILE_BYTES
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

router = APIRouter(prefix="/app/stock", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.STOCK))])

STATUSES = {"draft": "Чернетка", "ordered": "Замовлено", "received": "Отримано", "cancelled": "Скасовано"}


def _text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _column(form: FormData, name: str) -> list[str]:
    return [str(value) for value in form.getlist(name)]


# ── Consumables ──


@router.get("")
async def item_list(
    request: Request, repository: Repository, search: str = "", category: str = "", low: str = ""
) -> HTMLResponse:
    items = await repository.list_items(StockItemFilter(number(category), below_minimum_only=bool(low)))
    needle = search.strip().casefold()
    items = [item for item in items if needle in item.name.casefold()]  # few items: the name filter is in Python
    if is_fragment(request):
        return render(request, "stock/_items.html", items=items)
    return render(
        request,
        "stock/items.html",
        items=items,
        categories=await repository.list_categories(),
        search=search,
        category_id=number(category),
        low=bool(low),
    )


async def _item_form(
    request: Request,
    repository: Repository,
    values: dict[str, Any],
    item_id: int | None = None,
    error: str | None = None,
) -> HTMLResponse:
    return render(
        request,
        "stock/item_form.html",
        status_code=200 if error is None else 400,
        values=values,
        item_id=item_id,
        error=error,
        categories=await repository.list_categories(),
        suppliers=await repository.list_suppliers(),
    )


def _item_values(item: StockItemDto) -> dict[str, Any]:
    """The form fields of a saved item, as the browser would send them."""
    return {
        "name": item.name,
        "unit": item.unit,
        "category_id": str(item.category_id or ""),
        "supplier_id": str(item.supplier_id or ""),
        "min_quantity": qty(item.min_quantity),
        "price": qty(item.price),
        "used_for": item.used_for,
    }


def _item_request(form: FormData) -> SaveStockItemRequest:
    return SaveStockItemRequest(
        name=_text(form, "name"),
        unit=_text(form, "unit"),
        category_id=number(_text(form, "category_id")),
        supplier_id=number(_text(form, "supplier_id")),
        min_quantity=decimal_field(_text(form, "min_quantity"), "Мінімум"),
        price=decimal_field(_text(form, "price"), "Ціна"),
        used_for=_text(form, "used_for"),
    )


@router.get("/items/new")
async def new_item_page(request: Request, repository: Repository) -> HTMLResponse:
    return await _item_form(request, repository, {"unit": "шт"})


@router.post("/items/new")
async def create_item(request: Request, repository: Repository, service: Stock, session: Session) -> HTMLResponse:
    form = await request.form()
    try:
        item_id = await service.create_item(_item_request(form))
    except BUSINESS_ERRORS as error:
        return await _item_form(request, repository, dict(form), error=await undo(session, error))
    raise Redirect(f"/app/stock/items/{item_id}")


@router.get("/items/{item_id}/edit")
async def edit_item_page(request: Request, item_id: int, repository: Repository) -> HTMLResponse:
    item = await repository.get_item_dto(item_id)
    if item is None:
        return render(request, "not_found.html", status_code=404)
    return await _item_form(request, repository, _item_values(item), item_id)


@router.post("/items/{item_id}/edit")
async def update_item(
    request: Request, item_id: int, repository: Repository, service: Stock, session: Session
) -> HTMLResponse:
    form = await request.form()
    try:
        await service.update_item(item_id, _item_request(form))
    except BUSINESS_ERRORS as error:
        return await _item_form(request, repository, dict(form), item_id, await undo(session, error))
    raise Redirect(f"/app/stock/items/{item_id}")


async def _item_card(
    request: Request,
    item_id: int,
    repository: Repository,
    places: ByWarehouse,
    template: str = "stock/_item.html",
    **messages: Any,
) -> HTMLResponse:
    item = await repository.get_item_dto(item_id)
    if item is None:
        return render(request, "not_found.html", status_code=404)
    return render(
        request,
        template,
        item=item,
        movements=await repository.list_movements(item_id),
        placement=await places.get(item_id),
        transfers=await places.transfers(item_id),
        warehouses=await places.warehouses(),
        **messages,
    )


@router.get("/items/{item_id}")
async def item_card(request: Request, item_id: int, repository: Repository, places: ByWarehouse) -> HTMLResponse:
    return await _item_card(request, item_id, repository, places, "stock/item.html")


@router.post("/items/{item_id}/min-quantity")
async def set_min_quantity(
    request: Request, item_id: int, repository: Repository, places: ByWarehouse, service: Stock, session: Session
) -> HTMLResponse:
    form = await request.form()
    try:
        await service.set_min_quantity(item_id, decimal_field(_text(form, "min_quantity"), "Мінімум"))
    except BUSINESS_ERRORS as error:
        return await _item_card(request, item_id, repository, places, error=await undo(session, error))
    return await _item_card(request, item_id, repository, places, saved="Мінімальний залишок змінено.")


@router.post("/items/{item_id}/adjustments")
async def adjust(
    request: Request,
    item_id: int,
    user: WebUser,
    repository: Repository,
    places: ByWarehouse,
    service: Stock,
    session: Session,
) -> HTMLResponse:
    """Receipt or use; at the main warehouse without a choice, else at the chosen one (AdjustStockDialog)."""
    form = await request.form()
    income = _text(form, "kind") != "out"
    try:
        quantity = decimal_field(_text(form, "quantity"), "Кількість")
        reason = _text(form, "reason") or ("Прихід" if income else "Витрата")
        change = AdjustStockRequest(delta=quantity if income else -quantity, reason=reason)
        warehouse_id = number(_text(form, "warehouse_id"))
        if warehouse_id is None:
            await service.adjust(item_id, change, user.user_id, user.username)
        else:
            await places.adjust(item_id, warehouse_id, change, user.user_id, user.username)
    except BUSINESS_ERRORS as error:
        return await _item_card(request, item_id, repository, places, error=await undo(session, error))
    return await _item_card(request, item_id, repository, places, saved="Записано в журнал руху.")


@router.post("/items/{item_id}/transfers")
async def transfer(
    request: Request, item_id: int, user: WebUser, repository: Repository, places: ByWarehouse, session: Session
) -> HTMLResponse:
    form = await request.form()
    try:
        move = TransferStockRequest(
            from_warehouse_id=number(_text(form, "from_warehouse_id")),  # empty: the main warehouse
            to_warehouse_id=number(_text(form, "to_warehouse_id")),
            quantity=decimal_field(_text(form, "quantity"), "Кількість"),
            comment=_text(form, "comment"),
        )
        await places.transfer(item_id, move, user.user_id, user.username)
    except BUSINESS_ERRORS as error:
        return await _item_card(request, item_id, repository, places, error=await undo(session, error))
    return await _item_card(request, item_id, repository, places, saved="Переміщено.")


@router.get("/by-warehouse")
async def by_warehouse(request: Request, places: ByWarehouse) -> HTMLResponse:
    return render(request, "stock/by_warehouse.html", report=await places.by_warehouse())


# ── Stocktaking: download a sheet, count, upload it, check the differences, apply ──


@router.get("/stocktaking")
async def stocktaking_page(request: Request, repository: Repository, places: ByWarehouse) -> HTMLResponse:
    return render(
        request,
        "stock/stocktaking.html",
        categories=await repository.list_categories(),
        warehouses=await places.warehouses(),
    )


@router.get("/stocktaking/sheet.xlsx")
async def stocktaking_sheet(service: Stocktaking, category: str = "", warehouse: str = "") -> Response:
    return xlsx_file(await service.sheet(number(category), number(warehouse)), "inventaryzatsiia")


@router.post("/stocktaking/preview")
async def stocktaking_preview(request: Request, service: Stocktaking) -> HTMLResponse:
    # A multipart form: the file and the warehouse; UploadFile is like IFormFile.
    form = await request.form()
    warehouse_id = number(_text(form, "warehouse_id"))
    upload = form.get("file")
    try:
        if not isinstance(upload, UploadFile) or not upload.filename:
            raise DomainError("Виберіть заповнений аркуш (.xlsx).")
        content = await upload.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise DomainError("Файл більший за 5 МБ.")
        preview = await service.preview(content, warehouse_id)
    except BUSINESS_ERRORS as error:
        return render(request, "stock/_stocktaking.html", error=str(error))
    return render(request, "stock/_stocktaking.html", preview=preview, warehouse_id=warehouse_id)


@router.post("/stocktaking")
async def apply_stocktaking(request: Request, user: WebUser, service: Stocktaking, session: Session) -> HTMLResponse:
    form = await request.form()
    try:
        counts = [
            StocktakingCountRequest(item_id=number(item_id) or 0, actual=decimal_field(actual, "Фактично"))
            for item_id, actual in zip(_column(form, "item_id"), _column(form, "actual"), strict=True)
        ]
        apply = ApplyStocktakingRequest(
            counts=counts, comment=_text(form, "comment"), warehouse_id=number(_text(form, "warehouse_id"))
        )
        result = await service.apply(apply, user.user_id, user.username)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    response = render(request, "stock/_stocktaking.html", result=result)
    response.headers["HX-Retarget"] = "#preview"  # the applied sheet is replaced, so it cannot be applied twice
    return response


# ── Reorder ──


async def _reorder_page(request: Request, service: Reorder, error: str | None = None) -> HTMLResponse:
    report = await service.get_report()
    return render(request, "stock/reorder.html", status_code=200 if error is None else 400, report=report, error=error)


@router.get("/reorder")
async def reorder_page(request: Request, service: Reorder) -> HTMLResponse:
    return await _reorder_page(request, service)


@router.post("/reorder/{supplier_id}")
async def order_from_reorder(
    request: Request, supplier_id: int, user: WebUser, orders: Orders, reorder: Reorder, session: Session
) -> HTMLResponse:
    try:
        order_id = await orders.create_from_reorder(supplier_id, user.user_id)
    except BUSINESS_ERRORS as error:
        return await _reorder_page(request, reorder, await undo(session, error))
    raise Redirect(f"/app/stock/purchase-orders/{order_id}")


# ── Purchase orders ──


@router.get("/purchase-orders")
async def purchase_order_list(request: Request, repository: PurchaseOrders, status: str = "") -> HTMLResponse:
    return render(
        request,
        "stock/purchase_orders.html",
        orders=await repository.list_orders(status if status in STATUSES else None),
        status=status,
        statuses=STATUSES,
    )


def _order_lines(form: FormData) -> list[PurchaseOrderLineRequest]:
    columns = (_column(form, "item_id"), _column(form, "quantity"), _column(form, "price"))
    return [
        PurchaseOrderLineRequest(
            item_id=number(item_id) or 0,
            quantity=decimal_field(quantity, "Кількість"),
            price=decimal_field(price, "Ціна"),
        )
        for item_id, quantity, price in zip(*columns, strict=True)
    ]


@router.get("/purchase-orders/new")
async def new_purchase_order_page(request: Request, repository: Repository) -> HTMLResponse:
    return render(
        request,
        "stock/purchase_order_new.html",
        suppliers=await repository.list_suppliers(),
        items=await repository.list_items(StockItemFilter()),
    )


@router.get("/purchase-orders/line")
async def purchase_order_line(request: Request, repository: Repository, add: str = "") -> HTMLResponse:
    """A line row for the draft editor; the server checks it only when the lines are saved."""
    item = await repository.get_item_dto(number(add) or 0)
    if item is None:
        return HTMLResponse("")
    return render(request, "stock/_purchase_line.html", item_id=item.id, name=item.name, unit=item.unit,
                  quantity="1", price=qty(item.price))  # fmt: skip


@router.post("/purchase-orders/new")
async def create_purchase_order(request: Request, user: WebUser, orders: Orders, session: Session) -> HTMLResponse:
    form = await request.form()
    try:
        create = CreatePurchaseOrderRequest(
            supplier_id=number(_text(form, "supplier_id")) or 0,
            lines=_order_lines(form),
            comment=_text(form, "comment"),
        )
        order_id = await orders.create(create, user.user_id)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    raise Redirect(f"/app/stock/purchase-orders/{order_id}")


async def _order_card(
    request: Request,
    order_id: int,
    repository: PurchaseOrders,
    stock: Repository,
    places: ByWarehouse,
    template: str = "stock/_purchase_order.html",
    **messages: Any,
) -> HTMLResponse:
    order = await repository.get_dto(order_id)
    if order is None:
        return render(request, "not_found.html", status_code=404)
    return render(
        request,
        template,
        order=order,
        status_name=STATUSES.get(order.status, order.status),
        items=await stock.list_items(StockItemFilter()) if order.status == "draft" else [],
        warehouses=await places.warehouses(),
        **messages,
    )


@router.get("/purchase-orders/{order_id}")
async def purchase_order_card(
    request: Request, order_id: int, repository: PurchaseOrders, stock: Repository, places: ByWarehouse
) -> HTMLResponse:
    return await _order_card(request, order_id, repository, stock, places, "stock/purchase_order.html")


@router.post("/purchase-orders/{order_id}/{action}")
async def purchase_order_action(
    request: Request,
    order_id: int,
    action: str,
    user: WebUser,
    orders: Orders,
    repository: PurchaseOrders,
    stock: Repository,
    places: ByWarehouse,
    session: Session,
) -> HTMLResponse:
    """The buttons of the card: save the draft lines, mark it ordered, receive goods, cancel."""
    form = await request.form()
    try:
        if action == "lines":
            await orders.set_lines(order_id, SetPurchaseOrderLinesRequest(lines=_order_lines(form)))
            saved = "Рядки збережено."
        elif action == "ordered":
            await orders.mark_ordered(order_id)
            saved = "Позначено як надіслане постачальнику."
        elif action == "receive":
            lines = [
                ReceiptLineRequest(line_id=number(line_id) or 0, quantity=decimal_field(quantity, "Приїхало"))
                for line_id, quantity in zip(_column(form, "line_id"), _column(form, "quantity"), strict=True)
                if quantity.strip() not in ("", "0")  # only what came now
            ]
            receive = ReceivePurchaseOrderRequest(lines=lines, warehouse_id=number(_text(form, "warehouse_id")))
            await orders.receive(order_id, receive, user.user_id, user.username)
            saved = "Прийнято на склад."
        elif action == "cancel":
            await orders.cancel(order_id)
            saved = "Замовлення скасовано."
        else:
            return render(request, "not_found.html", status_code=404)
    except BUSINESS_ERRORS as error:
        return await _order_card(request, order_id, repository, stock, places, error=await undo(session, error))
    return await _order_card(request, order_id, repository, stock, places, saved=saved)


# ── Suppliers and categories ──


async def _suppliers_page(
    request: Request, repository: Repository, error: str | None = None, values: dict[str, Any] | None = None
) -> HTMLResponse:
    return render(
        request,
        "stock/suppliers.html",
        status_code=200 if error is None else 400,
        suppliers=await repository.list_suppliers(),
        categories=await repository.list_categories(),
        error=error,
        values=values or {},
    )


@router.get("/suppliers")
async def suppliers_page(request: Request, repository: Repository) -> HTMLResponse:
    return await _suppliers_page(request, repository)


@router.post("/suppliers")
async def create_supplier(request: Request, repository: Repository, service: Stock, session: Session) -> HTMLResponse:
    form = await request.form()
    fields = ("name", "phone", "email", "categories", "notes")
    try:
        await service.create_supplier(CreateStockSupplierRequest(**{name: _text(form, name) for name in fields}))
    except BUSINESS_ERRORS as error:
        return await _suppliers_page(request, repository, await undo(session, error), dict(form))
    raise Redirect("/app/stock/suppliers")


@router.post("/categories")
async def create_category(request: Request, repository: Repository, service: Stock, session: Session) -> HTMLResponse:
    form = await request.form()
    try:
        await service.create_category(CreateStockCategoryRequest(name=_text(form, "category_name")))
    except BUSINESS_ERRORS as error:
        return await _suppliers_page(request, repository, await undo(session, error), dict(form))
    raise Redirect("/app/stock/suppliers")
