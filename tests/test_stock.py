from tests.helpers import create, open_book, save_book, upload
from tests.seed import ADMIN, MANAGER, STOREKEEPER


def item(name: str, unit: str = "м", min_quantity: float = 0, price: float = 0, **extra) -> dict:
    return {"categoryId": None, "supplierId": None, "name": name, "unit": unit, "minQuantity": min_quantity,
            "price": price} | extra  # fmt: skip


def test_create_adjust_and_read_movements(api):
    client = api(ADMIN)
    item_id = create(client, "/stock/items", item("Льон", min_quantity=10, categoryId=1))

    low = client.get("/stock/items?belowMinimumOnly=true").json()
    assert item_id in [i["id"] for i in low]

    assert client.post(f"/stock/items/{item_id}/adjustments", json={"delta": 15, "reason": "Прихід"}).status_code == 200
    too_much = client.post(f"/stock/items/{item_id}/adjustments", json={"delta": -20, "reason": "Пошиття"})
    assert too_much.status_code == 400

    stock_item = client.get(f"/stock/items/{item_id}").json()
    assert (stock_item["quantity"], stock_item["isBelowMinimum"], stock_item["categoryName"]) == (15, False, "Тканини")
    [movement] = client.get(f"/stock/items/{item_id}/movements").json()
    assert (movement["delta"], movement["quantityBefore"], movement["quantityAfter"]) == (15, 0, 15)
    assert movement["username"] == ADMIN


def test_update_item_changes_supplier_and_name_keeps_quantity(api):
    client = api(ADMIN)
    supplier = create(client, "/stock/suppliers", {"name": "Нитки-Опт"})
    item_id = create(client, "/stock/items", item("Нитки", "шт", 5, categoryId=1))
    client.post(f"/stock/items/{item_id}/adjustments", json={"delta": 7, "reason": "Прихід"})

    update = client.put(
        f"/stock/items/{item_id}", json=item("Нитки чорні", "шт", 10, 25, categoryId=1, supplierId=supplier)
    )
    assert update.status_code == 204

    stock_item = client.get(f"/stock/items/{item_id}").json()
    assert (stock_item["name"], stock_item["supplierName"], stock_item["minQuantity"]) == (
        "Нитки чорні",
        "Нитки-Опт",
        10,
    )
    assert (stock_item["price"], stock_item["quantity"]) == (25, 7)
    unknown = client.put(f"/stock/items/{item_id}", json=item("Нитки", "шт", supplierId=9999))
    assert unknown.status_code == 400


def test_malformed_json_is_bad_request_not_server_error(api):
    response = api(ADMIN).post("/stock/items", content=b'{ "name": ', headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert "JSON" in response.text


def test_unknown_item_returns_404(api):
    assert api(ADMIN).get("/stock/items/99999").status_code == 404


def test_roles_limit_access_to_stock(api):
    assert api(STOREKEEPER).get("/stock/items").status_code == 200
    assert api(MANAGER).get("/stock/items").status_code == 403


def test_transfer_adjust_report_and_warehouse_with_stock_cannot_be_deleted(api):
    client = api(ADMIN)
    warehouse = create(client, "/locations", {"name": "Склад на Подолі"})
    item_id = create(client, "/stock/items", item("Льон для складів", price=100, categoryId=1))
    client.post(f"/stock/items/{item_id}/adjustments", json={"delta": 30, "reason": "Прихід"})

    moved = client.post(
        f"/stock/items/{item_id}/transfers", json={"fromWarehouseId": None, "toWarehouseId": warehouse, "quantity": 10}
    )
    assert moved.status_code == 200
    places = moved.json()
    assert places["total"] == 30
    assert [(p["warehouseId"], p["quantity"]) for p in places["places"]] == [(None, 20), (warehouse, 10)]

    main_too_much = client.post(f"/stock/items/{item_id}/adjustments", json={"delta": -25, "reason": "Пошиття"})
    assert main_too_much.status_code == 400
    url = f"/stock/items/{item_id}/warehouses/{warehouse}/adjustments"
    assert client.post(url, json={"delta": -11, "reason": "Брак"}).status_code == 400
    used = client.post(url, json={"delta": -4, "reason": "Брак"})
    assert used.status_code == 200
    assert "Склад на Подолі" in used.json()["reason"]

    report = client.get("/stock/by-warehouse").json()
    column = next(i for i, p in enumerate(report["places"]) if p["warehouseId"] == warehouse)
    row = next(r for r in report["rows"] if r["itemId"] == item_id)
    assert (row["quantities"][0], row["quantities"][column]) == (20, 6)
    assert report["places"][column]["value"] == 600

    [transfer] = client.get(f"/stock/items/{item_id}/transfers").json()
    assert (transfer["from"], transfer["to"]) == ("Основний склад", "Склад на Подолі")
    assert client.delete(f"/locations/{warehouse}").status_code == 400


def test_receive_purchase_order_to_warehouse_puts_delivery_there(api):
    client = api(ADMIN)
    warehouse = create(client, "/locations", {"name": "Склад у Львові"})
    supplier = create(client, "/stock/suppliers", {"name": "Фурнітура-Львів"})
    item_id = create(client, "/stock/items", item("Ґудзики", "шт", categoryId=1, supplierId=supplier))
    order = create(
        client, "/stock/purchase-orders", {"supplierId": supplier, "lines": [{"itemId": item_id, "quantity": 50}]}
    )
    assert client.post(f"/stock/purchase-orders/{order}/ordered").status_code == 204
    [line] = client.get(f"/stock/purchase-orders/{order}").json()["lines"]

    received = client.post(
        f"/stock/purchase-orders/{order}/receive",
        json={"lines": [{"lineId": line["id"], "quantity": 50}], "warehouseId": warehouse},
    )
    assert received.status_code == 204

    places = client.get(f"/stock/items/{item_id}/places").json()["places"]
    assert [(p["warehouseId"], p["quantity"]) for p in places] == [(None, 0), (warehouse, 50)]


def test_reorder_report_groups_items_below_minimum_by_supplier(api):
    client = api(STOREKEEPER)
    supplier = create(client, "/stock/suppliers", {"name": "Текстиль-Опт", "phone": "+380441234567"})
    linen = create(client, "/stock/items", item("Льон", min_quantity=30, price=200, supplierId=supplier))
    client.post(f"/stock/items/{linen}/adjustments", json={"delta": 12.5, "reason": "Прихід"})
    create(client, "/stock/items", item("Пакет", "шт", min_quantity=10))

    report = client.get("/stock/reorder").json()

    textile = next(s for s in report["suppliers"] if s["supplierId"] == supplier)
    [line] = textile["lines"]
    assert (line["suggested"], line["sum"]) == (48, 9600)  # 2 × 30 − 12.5 = 47.5 → 48 m at 200
    assert "— Льон: 48 м" in textile["messageText"]
    assert report["suppliers"][-1]["supplierName"] == "Без постачальника"


def test_purchase_order_from_reorder_ordered_received_in_parts(api):
    client = api(STOREKEEPER)
    supplier = create(client, "/stock/suppliers", {"name": "Фурнітура-Опт"})
    zipper = create(client, "/stock/items", item("Блискавка", "шт", min_quantity=50, price=5, supplierId=supplier))

    order_id = create(client, f"/stock/purchase-orders/from-reorder/{supplier}", {})
    order = client.get(f"/stock/purchase-orders/{order_id}").json()
    [line] = order["lines"]
    assert (order["status"], line["quantity"], order["total"]) == ("draft", 100, 500)
    assert "— Блискавка: 100 шт" in order["messageText"]
    assert supplier not in [s["supplierId"] for s in client.get("/stock/reorder").json()["suppliers"]]

    assert client.post(f"/stock/purchase-orders/{order_id}/ordered").status_code == 204

    def receive(quantity: int):
        return client.post(
            f"/stock/purchase-orders/{order_id}/receive", json={"lines": [{"lineId": line["id"], "quantity": quantity}]}
        )

    assert receive(60).status_code == 204
    order = client.get(f"/stock/purchase-orders/{order_id}").json()
    assert (order["status"], order["lines"][0]["remaining"]) == ("ordered", 40)
    assert receive(40).status_code == 204
    assert client.get(f"/stock/purchase-orders/{order_id}").json()["status"] == "received"

    assert client.get(f"/stock/items/{zipper}").json()["quantity"] == 100
    movements = client.get(f"/stock/items/{zipper}/movements").json()
    assert {m["reason"] for m in movements} == {f"Прихід за замовленням постачальнику №{order_id}"}
    assert receive(1).status_code == 400

    summaries = [s for s in client.get("/stock/purchase-orders?status=received").json() if s["id"] == order_id]
    assert [(s["id"], s["lineCount"], s["total"]) for s in summaries] == [(order_id, 1, 500)]


def test_stocktaking_sheet_preview_apply_writes_differences_to_log(api):
    client = api(STOREKEEPER)
    item_id = create(client, "/stock/items", item("Льон для інвентаризації", price=50))
    client.post(f"/stock/items/{item_id}/adjustments", json={"delta": 10, "reason": "Прихід"})

    book = open_book(client.get("/stock/stocktaking/sheet.xlsx").content)
    sheet = book.worksheets[0]
    assert sheet.cell(1, 6).value == "Фактично"
    row = next(r for r in sheet.iter_rows(min_row=2) if r[0].value == item_id)
    assert row[4].value == 10
    row[5].value = "7,5"

    preview = upload(client, "/stock/stocktaking/preview", save_book(book)).json()
    assert preview["errors"] == []
    [line] = preview["lines"]
    assert (line["booked"], line["actual"], line["difference"]) == (10, 7.5, -2.5)

    applied = client.post(
        "/stock/stocktaking", json={"counts": [{"itemId": item_id, "actual": 7.5}], "comment": "жовтень"}
    ).json()
    assert (applied["counted"], applied["changed"], applied["value"]) == (1, 1, -125)
    movement = client.get(f"/stock/items/{item_id}/movements").json()[0]
    assert (movement["delta"], movement["reason"]) == (-2.5, "Інвентаризація: жовтень")

    wrong = client.post("/stock/stocktaking", json={"counts": [{"itemId": item_id, "actual": -1}]})
    assert wrong.status_code == 400
