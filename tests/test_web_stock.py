"""Warehouse pages at /app/stock: consumables, their card, stocktaking, reorder, purchase orders, suppliers."""

import re

from tests.helpers import create, open_book, save_book
from tests.seed import ADMIN, MANAGER, STOREKEEPER

HTMX = {"HX-Request": "true"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def item_form(name: str, **changes: str) -> dict[str, str]:
    form = {"name": name, "unit": "м", "category_id": "1", "supplier_id": "", "min_quantity": "0", "price": "0"}
    return form | {"used_for": ""} | changes


def new_item(client, name: str, **changes: str) -> int:
    response = client.post("/app/stock/items/new", data=item_form(name, **changes), follow_redirects=False)
    assert response.status_code == 303, response.text
    return int(response.headers["location"].rsplit("/", 1)[1])


def quantity(client, item_id: int) -> float:
    return client.get(f"/stock/items/{item_id}").json()["quantity"]


def test_menu_and_access_follow_the_stock_role(api):
    storekeeper = api(STOREKEEPER).get("/app/stock")
    assert storekeeper.status_code == 200
    assert 'href="/app/stock/stocktaking"' in storekeeper.text
    assert 'href="/app/orders"' not in storekeeper.text

    manager = api(MANAGER)
    assert 'href="/app/stock"' not in manager.get("/app/").text
    assert manager.get("/app/stock/reorder").status_code == 403


def test_item_is_created_edited_and_found_by_name(api):
    client = api(STOREKEEPER)
    wrong = client.post("/app/stock/items/new", data=item_form("Віскоза", price="дорого"))
    assert wrong.status_code == 400
    assert "Ціна: потрібне число" in wrong.text
    assert 'value="Віскоза"' in wrong.text  # the form keeps what was typed

    item_id = new_item(client, "Віскоза веб", min_quantity="12,5", price="80")
    card = client.get(f"/app/stock/items/{item_id}")
    assert "Віскоза веб" in card.text
    assert "Нижче мінімуму" in card.text

    edit = client.get(f"/app/stock/items/{item_id}/edit")
    assert 'value="12,5"' in edit.text
    form = item_form("Віскоза чорна веб", price="85")
    saved = client.post(f"/app/stock/items/{item_id}/edit", data=form, follow_redirects=False)
    assert saved.status_code == 303
    assert client.get(f"/stock/items/{item_id}").json()["name"] == "Віскоза чорна веб"

    found = client.get("/app/stock?search=чорна веб", headers=HTMX)
    assert "Віскоза чорна веб" in found.text
    assert "<h1>" not in found.text  # only the table


def test_card_receipt_use_minimum_and_transfer(api):
    client = api(ADMIN)
    warehouse = create(client, "/locations", {"name": "Склад веб-тесту"})
    item_id = new_item(client, "Льон веб")
    url = f"/app/stock/items/{item_id}"

    received = client.post(f"{url}/adjustments", data={"kind": "in", "quantity": "30", "reason": ""}, headers=HTMX)
    assert "Записано в журнал руху." in received.text
    too_much = client.post(f"{url}/adjustments", data={"kind": "out", "quantity": "31", "reason": "Пошиття"})
    assert 'role="alert"' in too_much.text
    assert quantity(client, item_id) == 30
    [movement] = client.get(f"/stock/items/{item_id}/movements").json()
    assert movement["reason"] == "Прихід"  # an empty reason gets the default

    assert "Мінімальний залишок змінено." in client.post(f"{url}/min-quantity", data={"min_quantity": "40"}).text
    assert client.get(f"/stock/items/{item_id}").json()["isBelowMinimum"] is True

    form = {"from_warehouse_id": "", "to_warehouse_id": str(warehouse), "quantity": "10", "comment": "веб"}
    moved = client.post(f"{url}/transfers", data=form)
    assert "Переміщено." in moved.text
    places = client.get(f"/stock/items/{item_id}/places").json()["places"]
    assert [(p["warehouseId"], p["quantity"]) for p in places] == [(None, 20), (warehouse, 10)]
    assert "Склад веб-тесту" in client.get("/app/stock/by-warehouse").text


def test_reorder_draft_ordered_and_received_from_the_card(api):
    client = api(STOREKEEPER)
    supplier = create(client, "/stock/suppliers", {"name": "Гудзики-Веб"})
    item_id = new_item(client, "Гудзик веб", unit="шт", supplier_id=str(supplier), min_quantity="50", price="2")
    assert "Гудзики-Веб" in client.get("/app/stock/reorder").text

    created = client.post(f"/app/stock/reorder/{supplier}", follow_redirects=False)
    assert created.status_code == 303
    url = created.headers["location"]
    order_id = int(url.rsplit("/", 1)[1])
    assert "Чернетка" in client.get(url).text

    lines = {"item_id": str(item_id), "quantity": "60", "price": "2,5"}
    assert "Рядки збережено." in client.post(f"{url}/lines", data=lines).text
    no_lines = client.post(f"{url}/lines", data={})
    assert "хоча б одну позицію" in no_lines.text
    assert "Позначено як надіслане" in client.post(f"{url}/ordered").text

    [line] = client.get(f"/stock/purchase-orders/{order_id}").json()["lines"]
    receive = {"line_id": str(line["id"]), "quantity": "45", "warehouse_id": ""}
    assert "Прийнято на склад." in client.post(f"{url}/receive", data=receive).text
    assert quantity(client, item_id) == 45
    order = client.get(f"/stock/purchase-orders/{order_id}").json()
    assert (order["status"], order["total"]) == ("ordered", 150)

    listed = client.get("/app/stock/purchase-orders?status=ordered")
    assert f'href="/app/stock/purchase-orders/{order_id}"' in listed.text
    assert client.post(f"{url}/unknown").status_code == 404


def test_new_purchase_order_needs_lines(api):
    client = api(STOREKEEPER)
    supplier = create(client, "/stock/suppliers", {"name": "Нитки-Веб"})
    item_id = new_item(client, "Нитки веб", unit="шт", price="12")

    row = client.get(f"/app/stock/purchase-orders/line?add={item_id}", headers=HTMX)
    assert 'name="price" value="12"' in row.text

    empty = client.post("/app/stock/purchase-orders/new", data={"supplier_id": str(supplier)}, headers=HTMX)
    assert "хоча б одну позицію" in empty.text
    form = {"supplier_id": str(supplier), "item_id": str(item_id), "quantity": "5", "price": "12", "comment": ""}
    created = client.post("/app/stock/purchase-orders/new", data=form, headers=HTMX)
    order_id = int(re.search(r"/purchase-orders/(\d+)", created.headers["HX-Redirect"]).group(1))
    assert client.get(f"/stock/purchase-orders/{order_id}").json()["total"] == 60


def test_stocktaking_preview_and_apply(api):
    client = api(STOREKEEPER)
    item_id = new_item(client, "Льон для веб-інвентаризації", price="10")
    client.post(f"/app/stock/items/{item_id}/adjustments", data={"kind": "in", "quantity": "10", "reason": "Прихід"})

    sheet = client.get("/app/stock/stocktaking/sheet.xlsx")
    assert sheet.headers["content-type"] == XLSX
    book = open_book(sheet.content)
    row = next(r for r in book.worksheets[0].iter_rows(min_row=2) if r[0].value == item_id)
    row[5].value = 8

    nothing = client.post("/app/stock/stocktaking/preview", data={"warehouse_id": ""}, files={})
    assert "Виберіть заповнений аркуш" in nothing.text
    files = {"file": ("count.xlsx", save_book(book), XLSX)}
    preview = client.post("/app/stock/stocktaking/preview", data={"warehouse_id": ""}, files=files)
    assert "Льон для веб-інвентаризації" in preview.text
    assert "Застосувати" in preview.text

    apply = {"item_id": str(item_id), "actual": "8", "comment": "веб", "warehouse_id": ""}
    applied = client.post("/app/stock/stocktaking", data=apply, headers=HTMX)
    assert applied.headers["HX-Retarget"] == "#preview"
    assert "змінено 1" in applied.text
    assert quantity(client, item_id) == 8

    wrong = client.post("/app/stock/stocktaking", data={**apply, "actual": "-1"}, headers=HTMX)
    assert "HX-Retarget" not in wrong.headers
    assert 'role="alert"' in wrong.text


def test_suppliers_and_categories_are_added(api):
    client = api(STOREKEEPER)
    form = {"name": "Тканини-Веб", "phone": "+380000000000", "email": "", "categories": "льон", "notes": ""}
    assert client.post("/app/stock/suppliers", data=form, follow_redirects=False).status_code == 303
    assert client.post("/app/stock/categories", data={"category_name": "Веб-категорія"}).status_code == 200

    page = client.get("/app/stock/suppliers").text
    assert "Тканини-Веб" in page
    assert "Веб-категорія" in page
    assert client.post("/app/stock/suppliers", data={**form, "name": ""}).status_code == 400
