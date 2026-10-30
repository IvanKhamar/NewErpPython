"""Its own database: the test changes the lines and status of a seeded order."""

from tests.helpers import create
from tests.seed import (
    ACCEPTED,
    ADMIN,
    CATEGORY_ORDER_ID,
    CATEGORY_PRODUCT_ID,
    MANAGER,
    SIZE_M_OPTION_VALUE_ID,
    STOREKEEPER,
)
from tests.test_production import model_request, operation, put_ok

SIZE_M = 502  # product_option_value of size M of CATEGORY_PRODUCT_ID


def test_new_composition_replaces_lines_and_totals_and_recounts_sewn_stock(api):
    admin = api(ADMIN)
    model_id = create(admin, "/production/models", model_request("Сорочка", ("M", 0)))
    [size] = admin.get(f"/production/models/{model_id}").json()["sizes"]
    put_ok(admin, f"/production/models/{model_id}/links", {
        "productIds": [CATEGORY_PRODUCT_ID],
        "sizeOptions": [{"sizeId": size["id"], "optionValueId": SIZE_M_OPTION_VALUE_ID}],
    })  # fmt: skip
    operation(admin, model_id, "sewn", [(size["id"], 5)])
    assert admin.put(f"/orders/{CATEGORY_ORDER_ID}/status", json={"statusId": ACCEPTED}).status_code == 200

    def sewn() -> float:
        return admin.get(f"/production/models/{model_id}").json()["sizes"][0]["sewnStock"]

    assert sewn() == 4  # the accepted order took 1

    manager = api(MANAGER)
    [line] = manager.get(f"/orders/{CATEGORY_ORDER_ID}/composition").json()["lines"]
    assert (line["productId"], line["productOptionValueId"], line["quantity"], line["price"]) == (
        CATEGORY_PRODUCT_ID,
        SIZE_M,
        1,
        900,
    )

    # The customer wants 3 shirts and paid delivery: the order gives back 1 and takes 3.
    response = manager.put(
        f"/orders/{CATEGORY_ORDER_ID}/composition",
        json={
            "lines": [{"productId": CATEGORY_PRODUCT_ID, "productOptionValueId": SIZE_M, "quantity": 3, "price": 900}],
            "shippingCost": 70,
            "discount": 100,
            "prepayment": 500,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"total": 2670, "warnings": []}
    assert sewn() == 2

    details = manager.get(f"/orders/{CATEGORY_ORDER_ID}").json()
    assert (details["total"], details["statusId"]) == (2670, ACCEPTED)
    assert api(STOREKEEPER).get(f"/orders/{CATEGORY_ORDER_ID}/composition").status_code == 403
