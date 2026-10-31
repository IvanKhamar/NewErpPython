"""Size pre-orders. Its own database: the test changes the stock and limit of a catalog size."""

import asyncio
from datetime import date

import pytest

from newerp.application.catalog.pre_orders import PreOrderFilter, PreOrderService, UpdatePreOrderRequest
from newerp.application.common import NotFoundError
from newerp.domain.catalog import MAX_QUOTA, SizeAvailability, availability, settle, validate_quota
from newerp.domain.common import DomainError
from tests.helpers import create
from tests.seed import ADMIN, CATEGORY_ID, MANAGER, SIZE_M_OPTION_VALUE_ID, STOREKEEPER
from tests.test_production import model_request, put_ok

SIZE_M = 502  # product_option_value of size M of the product CATEGORY_PRODUCT_ID


# ═══ Rules ═══


@pytest.mark.parametrize(
    ("quantity", "quota", "subtract", "expected"),
    [
        (3, 0, True, SizeAvailability.IN_STOCK),
        (0, 5, True, SizeAvailability.PRE_ORDER),
        (-2, 5, True, SizeAvailability.PRE_ORDER),
        (0, 0, True, SizeAvailability.SOLD_OUT),
        (0, 0, False, SizeAvailability.IN_STOCK),
    ],
)
def test_availability_follows_stock_then_quota(quantity, quota, subtract, expected):
    assert availability(quantity, quota, subtract) == expected


@pytest.mark.parametrize(
    ("quantity", "quota", "expected"),
    [
        (-2, 5, (0, 3)),  # 2 of 5 sold ahead
        (-7, 5, (-2, 0)),  # sold over the limit: the limit is 0, the rest stays negative
        (4, 5, (4, 5)),  # there is stock: nothing is taken
        (-3, 0, (-3, 0)),
    ],
)
def test_settle_moves_sold_ahead_from_quota(quantity, quota, expected):
    assert settle(quantity, quota) == expected


def test_validate_quota_rejects_negative_and_huge():
    validate_quota(0)
    for quota in (-1, MAX_QUOTA + 1):
        with pytest.raises(DomainError):
            validate_quota(quota)


# ═══ Service ═══


class FakeStore:
    def __init__(self) -> None:
        self.last_list = None
        self.last_update = None

    async def list_rows(self, pre_order_filter, limit):
        self.last_list = (pre_order_filter, limit)
        return []

    async def update(self, product_option_value_id, quota, date_available):
        if product_option_value_id == 404:
            return False
        self.last_update = (product_option_value_id, quota, date_available)
        return True

    async def settle(self, product_ids):
        return 2 if product_ids is None else 0


def test_list_trims_search_and_limits():
    store = FakeStore()
    service = PreOrderService(store)

    asyncio.run(service.list_rows(PreOrderFilter(search="  сукня ", only_with_quota=True)))
    assert store.last_list == (PreOrderFilter(search="сукня", only_with_quota=True), 200)
    asyncio.run(service.list_rows(PreOrderFilter(search="   ")))
    assert store.last_list[0].search is None


def test_update_validates_quota_and_unknown_size():
    store = FakeStore()
    service = PreOrderService(store)

    with pytest.raises(DomainError):
        asyncio.run(service.update(1, UpdatePreOrderRequest(quota=-1)))
    with pytest.raises(NotFoundError):
        asyncio.run(service.update(404, UpdatePreOrderRequest(quota=3)))
    asyncio.run(service.update(1, UpdatePreOrderRequest(quota=3, date_available=date(2026, 11, 1))))
    assert store.last_update == (1, 3, date(2026, 11, 1))


def test_settle_covers_all_products():
    assert asyncio.run(PreOrderService(FakeStore()).settle()).sizes == 2


# ═══ API ═══


def set_quantity(db, quantity: int) -> None:
    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE dd_product_option_value SET quantity = %s, subtract = 1 WHERE product_option_value_id = %s",
            (quantity, SIZE_M),
        )


def size(db) -> tuple[int, int]:
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT quantity, pre_order_quantity FROM dd_product_option_value WHERE product_option_value_id = %s",
            (SIZE_M,),
        )
        return tuple(cursor.fetchone())


def test_quota_is_editable_and_sold_ahead_is_settled_also_before_storefront_sync(api, db):
    manager = api(MANAGER)
    assert manager.put(f"/preorders/{SIZE_M}", json={"quota": 5, "dateAvailable": "2026-11-20"}).status_code == 204
    assert manager.put(f"/preorders/{SIZE_M}", json={"quota": -1, "dateAvailable": None}).status_code == 400
    assert manager.put("/preorders/9999", json={"quota": 1}).status_code == 404

    # The storefront sold 2 pieces ahead: the stock went negative.
    set_quantity(db, -2)
    [row] = manager.get("/preorders?search=лляна&onlyWithQuota=true").json()
    assert (row["productOptionValueId"], row["size"], row["quantity"], row["quota"]) == (SIZE_M, "M", -2, 5)
    assert (row["dateAvailable"], row["availability"]) == ("2026-11-20", SizeAvailability.PRE_ORDER)

    assert manager.post("/preorders/settle").json() == {"sizes": 1}
    assert size(db) == (0, 3)

    # Pushing sewn stock to the storefront first settles what was sold ahead, or the new stock would wipe the minus.
    set_quantity(db, -1)
    admin = api(ADMIN)
    model_id = create(admin, "/production/models", model_request("Сорочка лляна", ("M", 0)))
    [model_size] = admin.get(f"/production/models/{model_id}").json()["sizes"]
    put_ok(admin, f"/production/models/{model_id}/links", {
        "categories": [{"categoryId": CATEGORY_ID, "nameFilter": "", "matchExact": False}],
        "sizeOptions": [{"sizeId": model_size["id"], "optionValueId": SIZE_M_OPTION_VALUE_ID}],
    })  # fmt: skip
    assert size(db) == (0, 2)

    assert manager.put(f"/preorders/{SIZE_M}", json={"quota": 0, "dateAvailable": None}).status_code == 204
    assert manager.get("/preorders?onlyWithQuota=true").json() == []
    [row] = manager.get("/preorders?search=лляна").json()
    assert row["dateAvailable"] is None

    assert api(STOREKEEPER).get("/preorders").status_code == 403
