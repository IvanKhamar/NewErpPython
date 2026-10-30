from tests.helpers import create
from tests.seed import ADMIN, CATEGORY_ID, CATEGORY_PRODUCT_ID, SIZE_M_OPTION_VALUE_ID
from tests.test_production import model_request, operation, put_ok


def test_storefront_shows_sewn_and_shipped(api):
    admin = api(ADMIN)
    model_id = create(admin, "/production/models", model_request("Сорочка лляна", ("S", 0), ("M", 0)))
    size_id = next(s["id"] for s in admin.get(f"/production/models/{model_id}").json()["sizes"] if s["name"] == "M")
    put_ok(admin, f"/production/models/{model_id}/links", {
        "categories": [{"categoryId": CATEGORY_ID, "nameFilter": "", "matchExact": False}],
        "sizeOptions": [{"sizeId": size_id, "optionValueId": SIZE_M_OPTION_VALUE_ID}],
    })  # fmt: skip

    def storefront() -> tuple[int, int]:
        [product] = admin.get("/orders/new/products?search=лляна").json()
        assert product["id"] == CATEGORY_PRODUCT_ID
        return product["options"][0]["quantity"], product["quantity"]

    assert storefront() == (0, 0)  # right after linking: nothing sewn yet
    operation(admin, model_id, "sewn", [(size_id, 3)])
    assert storefront() == (3, 3)
    operation(admin, model_id, "ship", [(size_id, 1)])
    assert storefront() == (2, 2)

    sync = admin.post("/production/storefront/sync").json()
    assert sync == {"models": 1, "products": 1}
