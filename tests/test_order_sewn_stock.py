"""Its own database: the tests change the statuses of seeded orders."""

from tests.helpers import create
from tests.seed import (
    ACCEPTED,
    ADMIN,
    CANCELLED,
    CATEGORY_ID,
    CATEGORY_ORDER_ID,
    MANUFACTURING,
    ORDER_ID,
    PRODUCT_ID,
    SIZE_M_OPTION_VALUE_ID,
    STOREKEEPER,
)
from tests.test_production import model_request, operation, put_ok


def change_status(client, status_id: int, order_id: int = ORDER_ID) -> list[str]:
    response = client.put(f"/orders/{order_id}/status", json={"statusId": status_id})
    assert response.status_code == 200, response.text
    return response.json()["warnings"]


def test_statuses_take_sewn_by_size_keep_inside_set_return_only_what_was_taken(api):
    storekeeper, admin = api(STOREKEEPER), api(ADMIN)
    model_id = create(storekeeper, "/production/models", model_request("Сукня лляна", ("S", 0), ("M", 0)))

    def size_m() -> dict:
        sizes = storekeeper.get(f"/production/models/{model_id}").json()["sizes"]
        return next(s for s in sizes if s["name"] == "M")

    size_id = size_m()["id"]
    put_ok(storekeeper, f"/production/models/{model_id}/links", {
        "productIds": [PRODUCT_ID], "sizeOptions": [{"sizeId": size_id, "optionValueId": SIZE_M_OPTION_VALUE_ID}],
    })  # fmt: skip
    operation(storekeeper, model_id, "sewn", [(size_id, 1)])  # only 1 sewn, 2 ordered

    [warning] = change_status(admin, ACCEPTED)
    assert "бракує 1 шт." in warning
    assert size_m()["sewnStock"] == 0

    operation(storekeeper, model_id, "sewn", [(size_id, 3)])
    assert change_status(admin, MANUFACTURING) == []  # inside the set: nothing changes
    assert size_m()["sewnStock"] == 3

    change_status(admin, CANCELLED)
    assert size_m()["sewnStock"] == 4  # only the 1 that was taken comes back

    log = storekeeper.get(f"/production/log?productionId={model_id}").json()
    assert any(
        r["comment"] == f"Замовлення #{ORDER_ID}" and r["direction"] == "out" and r["username"] == "system" for r in log
    )
    assert any(r["comment"] == f"Скасування #{ORDER_ID}" and r["direction"] == "in" and r["quantity"] == 1 for r in log)


def test_product_linked_through_category_name_filter_is_taken_too(api):
    storekeeper, admin = api(STOREKEEPER), api(ADMIN)
    model_id = create(storekeeper, "/production/models", model_request("Сорочка", ("M", 0)))
    [size] = storekeeper.get(f"/production/models/{model_id}").json()["sizes"]
    put_ok(storekeeper, f"/production/models/{model_id}/links", {
        "categories": [{"categoryId": CATEGORY_ID, "nameFilter": "ОВЕРСАЙЗ"}],
        "sizeOptions": [{"sizeId": size["id"], "optionValueId": SIZE_M_OPTION_VALUE_ID}],
    })  # fmt: skip
    operation(storekeeper, model_id, "sewn", [(size["id"], 5)])

    assert change_status(admin, ACCEPTED, CATEGORY_ORDER_ID) == []

    assert storekeeper.get(f"/production/models/{model_id}").json()["sizes"][0]["sewnStock"] == 4
