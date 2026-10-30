"""Its own database: the test changes an order's status."""

from tests.helpers import create
from tests.seed import (
    ACCEPTED,
    ADMIN,
    CATEGORY_ORDER_ID,
    MANAGER,
    ORDER_ID,
    PRODUCT_ID,
    SIZE_M_OPTION_VALUE_ID,
    STOREKEEPER,
    insert,
)
from tests.test_production import model_request, operation, put_ok

DRESS_CATEGORY_ID = 7


def test_plan_counts_what_active_orders_miss_minus_free_sewn_and_what_orders_hold(api, db):
    insert(db, "dd_category", category_id=DRESS_CATEGORY_ID, production_period=10, status=True, top=False, column=0,
           date_added="2026-01-01", date_modified="2026-01-01")  # fmt: skip
    insert(db, "dd_product_to_category", product_id=PRODUCT_ID, category_id=DRESS_CATEGORY_ID)
    storekeeper = api(STOREKEEPER)
    model_id = create(storekeeper, "/production/models", model_request("Сукня", ("S", 0), ("M", 0)))
    size_m = next(
        s["id"] for s in storekeeper.get(f"/production/models/{model_id}").json()["sizes"] if s["name"] == "M"
    )
    put_ok(storekeeper, f"/production/models/{model_id}/links", {
        "productIds": [PRODUCT_ID], "sizeOptions": [{"sizeId": size_m, "optionValueId": SIZE_M_OPTION_VALUE_ID}],
    })  # fmt: skip
    operation(storekeeper, model_id, "cut", [(size_m, 1)])

    plan = storekeeper.get("/production/plan").json()
    [row] = plan["rows"]
    assert (row["productionId"], row["sizeName"], row["ordered"], row["missing"]) == (model_id, "M", 2, 2)
    assert (row["toSew"], row["fromCut"], row["toCut"]) == (2, 1, 1)
    assert row["orderIds"] == [ORDER_ID]
    assert row["deadline"] == "2026-10-11"  # order date 2026-10-01 + 10 days
    assert any(CATEGORY_ORDER_ID in i["orderIds"] and "Не прив'язано" in i["reason"] for i in plan["issues"])

    operation(storekeeper, model_id, "sewn", [(size_m, 1)])
    assert api(ADMIN).put(f"/orders/{ORDER_ID}/status", json={"statusId": ACCEPTED}).status_code == 200
    [row] = storekeeper.get("/production/plan").json()["rows"]
    assert (row["ordered"], row["missing"], row["toSew"], row["sewnStock"]) == (2, 1, 1, 0)

    operation(storekeeper, model_id, "sewn", [(size_m, 1)])
    assert storekeeper.get("/production/plan").json()["rows"] == []
    [row] = storekeeper.get("/production/plan?onlyToSew=false").json()["rows"]
    assert row["toSew"] == 0

    assert api(MANAGER).get("/production/plan").status_code == 403
