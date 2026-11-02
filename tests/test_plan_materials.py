"""Consumables for the sewing plan: for active orders (as .NET) and for sewing up to the minimal stock. Its own
database: the test links a model to the order ORDER_ID."""

from decimal import Decimal

from newerp.domain.production import spread_by_share
from tests.helpers import create
from tests.seed import MANAGER, PRODUCT_ID, SIZE_M_OPTION_VALUE_ID, STOREKEEPER
from tests.test_production import model_request, operation, put_ok, stock_item


def test_spread_by_share_rounds_up_and_splits_evenly_without_shares():
    assert spread_by_share(Decimal(10), {1: Decimal(30), 2: Decimal(70)}) == {1: 3, 2: 7}
    assert spread_by_share(Decimal(5), {1: Decimal(50), 2: Decimal(50)}) == {1: 3, 2: 3}  # a little more, not short
    assert spread_by_share(Decimal(4), {1: Decimal(0), 2: Decimal(0)}) == {1: 2, 2: 2}
    assert spread_by_share(Decimal(0), {1: Decimal(50)}) == {}


def test_materials_for_orders_and_up_to_the_minimum(api):
    client = api(STOREKEEPER)
    linen = stock_item(client, "Льон для плану", "м", stock=2)
    thread = stock_item(client, "Нитки для плану", "шт")
    supplier = create(client, "/stock/suppliers", {"name": "Нитки-План"})
    create(client, "/stock/purchase-orders", {"supplierId": supplier, "lines": [{"itemId": thread, "quantity": 3}]})

    # The order ORDER_ID wants 2 dresses of size M; one M is already cut.
    model_id = create(client, "/production/models", model_request("Сукня плану", ("S", 0), ("M", 0), min_total=10))
    sizes = {s["name"]: s["id"] for s in client.get(f"/production/models/{model_id}").json()["sizes"]}
    put_ok(client, f"/production/models/{model_id}/links", {
        "productIds": [PRODUCT_ID], "sizeOptions": [{"sizeId": sizes["M"], "optionValueId": SIZE_M_OPTION_VALUE_ID}],
    })  # fmt: skip
    front = create(client, f"/production/models/{model_id}/components", {"name": "Перед"})
    put_ok(client, f"/production/components/{front}/bom", {"rows": [{"itemId": linen, "qtyPerUnit": 1.5}]})
    put_ok(client, f"/production/models/{model_id}/sewn-bom", {"rows": [{"itemId": thread, "qtyPerUnit": 2}]})
    operation(client, model_id, "cut", [(sizes["M"], 1)])

    def materials(scope: str) -> dict[str, dict]:
        report = client.get(f"/production/plan/materials?scope={scope}").json()
        return {m["name"]: m for m in report["materials"]}

    # For the order: cut 1 more (1.5 m; the first cut took 1.5 of 2), sew 2 (4 threads, 3 ordered from a supplier).
    orders = materials("orders")
    assert [orders["Льон для плану"][k] for k in ("needed", "inStock", "shortage")] == [1.5, 0.5, 1]
    assert [orders["Нитки для плану"][k] for k in ("needed", "onOrder", "shortage")] == [4, 3, 1]
    [use] = orders["Нитки для плану"]["uses"]
    assert (use["sizeName"], use["stage"], use["forMinimum"]) == ("M", "Пошив", False)
    assert (
        client.get("/production/plan/materials").json() == client.get("/production/plan/materials?scope=orders").json()
    )  # orders is the default, as in .NET

    # Up to the minimum of 10: the order takes nothing sewn, so 5 of each size; the cut M went to the order.
    minimum = materials("minimum")
    assert minimum["Льон для плану"]["needed"] == 15  # 10 parts to cut
    assert minimum["Нитки для плану"]["needed"] == 20
    assert all(u["forMinimum"] for u in minimum["Нитки для плану"]["uses"])
    assert materials("all")["Нитки для плану"]["needed"] == 24

    page = client.get("/app/production/plan/materials?scope=minimum").text
    assert "Льон для плану" in page
    assert "(до мінімуму)" in page
    assert client.get("/production/plan/materials?scope=wrong").status_code == 400
    assert api(MANAGER).get("/production/plan/materials").status_code == 403
