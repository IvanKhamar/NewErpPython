from tests.helpers import create
from tests.seed import ADMIN, MANAGER, PRODUCT_ID, STOREKEEPER


def model_request(name: str, *sizes: tuple[str, float], min_total: int = 50) -> dict:
    return {"name": name, "sizes": [{"name": n, "percent": p} for n, p in sizes], "minTotal": min_total}


def stock_item(client, name: str, unit: str, stock: float = 0) -> int:
    item_id = create(client, "/stock/items", {"name": name, "unit": unit, "minQuantity": 0})
    if stock:
        assert (
            client.post(f"/stock/items/{item_id}/adjustments", json={"delta": stock, "reason": "Прихід"}).status_code
            == 200
        )
    return item_id


def put_ok(client, url: str, body: dict) -> None:
    response = client.put(url, json=body)
    assert response.is_success, f"{url}: {response.status_code} {response.text}"


def operation(client, model_id: int, name: str, quantities: list[tuple[int, float]], **extra):
    body = {"quantities": [{"sizeId": s, "quantity": q} for s, q in quantities]} | extra
    return client.post(f"/production/models/{model_id}/{name}", json=body)


def test_model_sizes_components_boms_and_links_round_trip(api):
    client = api(STOREKEEPER)
    linen = stock_item(client, "Льон", "м")
    thread = stock_item(client, "Нитки", "шт")
    model_id = create(
        client, "/production/models", model_request("Сукня лляна", ("S", 30), ("M", 40), ("L", 30), min_total=20)
    )
    front = create(client, f"/production/models/{model_id}/components", {"name": "Перед"})
    size_m = next(s["id"] for s in client.get(f"/production/models/{model_id}").json()["sizes"] if s["name"] == "M")

    put_ok(client, f"/production/components/{front}/bom", {"rows": [
        {"sizeId": None, "itemId": linen, "qtyPerUnit": 1.2}, {"sizeId": size_m, "itemId": linen, "qtyPerUnit": 1.4},
    ]})  # fmt: skip
    put_ok(client, f"/production/models/{model_id}/sewn-bom", {"rows": [
        {"componentId": front, "qtyPerUnit": 1}, {"itemId": thread, "qtyPerUnit": 0.5},
    ]})  # fmt: skip
    put_ok(client, f"/production/models/{model_id}/links", {
        "productIds": [PRODUCT_ID], "categories": [{"categoryId": 5, "nameFilter": "сукня"}],
        "sizeOptions": [{"sizeId": size_m, "optionValueId": 46}],
    })  # fmt: skip
    put_ok(
        client,
        f"/production/models/{model_id}",
        model_request("Сукня лляна", ("M", 50), ("L", 30), ("XL", 20), min_total=20),
    )

    model = client.get(f"/production/models/{model_id}").json()
    assert [s["name"] for s in model["sizes"]] == ["M", "L", "XL"]
    assert (model["sizes"][0]["id"], model["sizes"][0]["optionValueId"]) == (size_m, 46)
    assert model["productIds"] == [PRODUCT_ID]
    [category] = model["categories"]
    assert category["nameFilter"] == "сукня"
    [component] = model["components"]
    assert next(b for b in component["bom"] if b["sizeId"] == size_m)["qtyPerUnit"] == 1.4
    assert component["bom"][0]["itemName"] == "Льон"
    assert len(model["sewnBom"]) == 2
    assert "Перед" in [b["componentName"] for b in model["sewnBom"]]

    below = client.get("/production/models?belowMinimumOnly=true").json()
    assert next(m for m in below if m["id"] == model_id)["deficit"] == 20  # 0 of 20 sewn

    assert client.delete(f"/production/components/{front}").status_code == 204
    model = client.get(f"/production/models/{model_id}").json()
    assert model["components"] == []
    [line] = model["sewnBom"]
    assert line["itemId"] == thread


def test_cut_and_sew_consume_boms_take_what_is_there_and_warn(api):
    client = api(STOREKEEPER)
    linen = stock_item(client, "Льон для крою", "м", 4)
    thread = stock_item(client, "Нитки для пошиву", "шт", 10)
    model_id = create(client, "/production/models", model_request("Сорочка", ("S", 0), ("M", 0)))
    front = create(client, f"/production/models/{model_id}/components", {"name": "Перед"})
    sizes = {s["name"]: s["id"] for s in client.get(f"/production/models/{model_id}").json()["sizes"]}
    put_ok(client, f"/production/components/{front}/bom", {"rows": [
        {"itemId": linen, "qtyPerUnit": 1}, {"sizeId": sizes["M"], "itemId": linen, "qtyPerUnit": 1.5},
    ]})  # fmt: skip
    put_ok(client, f"/production/models/{model_id}/sewn-bom", {"rows": [
        {"componentId": front, "qtyPerUnit": 1}, {"itemId": thread, "qtyPerUnit": 2},
    ]})  # fmt: skip

    cut = operation(client, model_id, "cut", [(sizes["M"], 2), (sizes["S"], 2)])
    assert cut.status_code == 200, cut.text
    [warning] = cut.json()["warnings"]
    assert "Бракує 1 м: Льон для крою" in warning
    assert client.get(f"/stock/items/{linen}").json()["quantity"] == 0

    sewn = operation(client, model_id, "sewn", [(sizes["M"], 3)])
    [warning] = sewn.json()["warnings"]
    assert "«Перед»" in warning
    assert client.get(f"/stock/items/{thread}").json()["quantity"] == 4

    model = client.get(f"/production/models/{model_id}").json()
    m = next(s for s in model["sizes"] if s["name"] == "M")
    assert (m["cutStock"], m["sewnStock"]) == (2, 3)  # sewing does not lower the cut stock: "to-sewing" does
    stock = model["components"][0]["stock"]
    assert (stock[str(sizes["M"])], stock[str(sizes["S"])]) == (0, 2)

    assert operation(client, model_id, "ship", [(sizes["M"], 5)]).status_code == 400
    assert operation(client, model_id, "to-sewing", [(sizes["M"], 2)]).status_code == 200

    log = client.get(f"/production/log?productionId={model_id}").json()
    assert len(log) == 4  # cut M and S, sewn M, to sewing M
    assert (log[0]["stage"], log[0]["direction"], log[0]["username"]) == ("cut", "out", STOREKEEPER)


def test_sewn_bom_unknown_item_is_bad_request(api):
    client = api(ADMIN)
    model_id = create(client, "/production/models", model_request("Футболка", ("S", 0)))

    response = client.put(
        f"/production/models/{model_id}/sewn-bom", json={"rows": [{"itemId": 99999, "qtyPerUnit": 1}]}
    )

    assert response.status_code == 400


def test_manager_has_no_access(api):
    assert api(MANAGER).get("/production/models").status_code == 403


def test_sewn_at_warehouse_transfer_ship_from_warehouse_report(api):
    client = api(ADMIN)
    warehouse = create(client, "/locations", {"name": "Шоурум"})
    model_id = create(client, "/production/models", model_request("Сукня для шоуруму", ("M", 100)))
    [size] = client.get(f"/production/models/{model_id}").json()["sizes"]
    size_m = size["id"]

    assert operation(client, model_id, "sewn", [(size_m, 6)]).status_code == 200
    assert operation(client, model_id, "sewn", [(size_m, 2)], warehouseId=warehouse).status_code == 200
    moved = client.post(
        f"/production/models/{model_id}/transfers",
        json={
            "sizeId": size_m,
            "fromWarehouseId": None,
            "toWarehouseId": warehouse,
            "quantity": 3,
            "comment": "На вітрину",
        },
    )
    assert moved.status_code == 200, moved.text

    [places] = client.get(f"/production/models/{model_id}/places").json()
    assert places["sewn"] == 8
    assert [(p["warehouseId"], p["quantity"]) for p in places["places"]] == [(None, 3), (warehouse, 5)]
    assert operation(client, model_id, "ship", [(size_m, 4)]).status_code == 400
    assert operation(client, model_id, "cut", [(size_m, 1)], warehouseId=warehouse).status_code == 400
    assert operation(client, model_id, "ship", [(size_m, 4)], warehouseId=warehouse).status_code == 200

    report = client.get("/production/by-warehouse").json()
    column = next(i for i, p in enumerate(report["places"]) if p["warehouseId"] == warehouse)
    row = next(r for r in report["rows"] if r["sizeId"] == size_m)
    assert (row["total"], row["quantities"][0], row["quantities"][column]) == (4, 3, 1)
    transfers = client.get(f"/production/models/{model_id}/transfers").json()
    assert (transfers[0]["sizeName"], transfers[0]["to"]) == ("M", "Шоурум")
    assert client.delete(f"/locations/{warehouse}").status_code == 400


def test_model_stocktaking_recounts_cut_sewn_and_components(api):
    client = api(STOREKEEPER)
    model_id = create(client, "/production/models", model_request("Сорочка для інвентаризації", ("M", 100)))
    front = create(client, f"/production/models/{model_id}/components", {"name": "Перед"})
    [size] = client.get(f"/production/models/{model_id}").json()["sizes"]
    operation(client, model_id, "sewn", [(size["id"], 4)])

    response = client.post(
        f"/production/models/{model_id}/stocktaking",
        json={
            "sizes": [{"sizeId": size["id"], "cut": 2, "sewn": 3}],
            "components": [{"componentId": front, "sizeId": size["id"], "actual": 6}],
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert (len(result["cut"]), len(result["sewn"]), result["components"]) == (1, 1, 1)

    model = client.get(f"/production/models/{model_id}").json()
    assert (model["sizes"][0]["cutStock"], model["sizes"][0]["sewnStock"]) == (2, 3)
    assert model["components"][0]["stock"][str(size["id"])] == 6
