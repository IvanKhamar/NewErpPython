"""Item 72: the reconstructed tables checked against the old site's CREATE scripts (F:\\PROJECTS\\VIDRO\\Vidro_DB).

The scripts gave the real types: consumables keep two decimals, names are longer than we guessed, and the product
order snapshot rows have their own key. These tests pin what changed with them.
"""

from datetime import datetime
from decimal import Decimal

import pytest

from newerp.domain.common import DomainError
from newerp.domain.partners import Partner, PartnerSettings, ProductMapping
from newerp.domain.stock import StockItem
from tests.helpers import create
from tests.seed import ADMIN

NOW = datetime(2026, 11, 8, 10, 0)


def stocked(quantity: str, name: str = "Льон") -> StockItem:
    item = StockItem(None, None, name, "м", Decimal(0))
    item.adjust(Decimal(quantity), "Прихід", 1, ADMIN, NOW)
    return item


# ═══ Consumables: decimal(10,2), longer names ═══


def test_consumable_change_keeps_two_decimals_like_the_old_table():
    item = stocked("5")

    with pytest.raises(DomainError, match="не більше 2 знаків після коми"):
        item.adjust(Decimal("0.005"), "Прихід", 1, ADMIN, NOW)

    assert item.quantity == 5


def test_production_write_off_is_rounded_half_up_to_two_decimals():
    item = stocked("5")

    movement = item.take_up_to(Decimal("1.125"), "Розкрій", 1, ADMIN, NOW)

    assert (movement.quantity, item.quantity) == (Decimal("1.13"), Decimal("3.87"))


def test_write_off_smaller_than_a_hundredth_takes_nothing():
    item = stocked("5")

    assert item.take_up_to(Decimal("0.004"), "Розкрій", 1, ADMIN, NOW) is None
    assert item.quantity == 5


def test_item_name_and_unit_may_be_as_long_as_the_real_columns():
    item = StockItem(None, None, "Н" * 255, "О" * 32, Decimal(0))

    assert (len(item.name), len(item.unit)) == (255, 32)
    with pytest.raises(DomainError):
        StockItem(None, None, "Н" * 256, "м", Decimal(0))


def test_three_decimals_are_refused_by_the_api_and_two_are_stored(api, db):
    client = api(ADMIN)
    item_id = create(client, "/stock/items", {"categoryId": 1, "name": "Стрічка", "unit": "м", "minQuantity": 0})

    refused = client.post(f"/stock/items/{item_id}/adjustments", json={"delta": 1.255, "reason": "Прихід"})
    stored = client.post(f"/stock/items/{item_id}/adjustments", json={"delta": 1.25, "reason": "Прихід"})

    assert (refused.status_code, stored.status_code) == (400, 200)
    with db.cursor() as cursor:
        cursor.execute("SELECT stock FROM dd_stock_items WHERE item_id = %s", (item_id,))
        assert cursor.fetchone()[0] == Decimal("1.25")


def test_deleting_a_category_leaves_its_items_without_one(api, db):
    """The real table has foreign keys with ON DELETE SET NULL, as the old module's deleteCategory expects."""
    client = api(ADMIN)
    category_id = create(client, "/stock/categories", {"name": "Тимчасова"})
    item_id = create(
        client, "/stock/items", {"categoryId": category_id, "name": "Скоби", "unit": "шт", "minQuantity": 0}
    )

    with db.cursor() as cursor:
        cursor.execute("DELETE FROM dd_stock_categories WHERE category_id = %s", (category_id,))
        cursor.execute("SELECT category_id FROM dd_stock_items WHERE item_id = %s", (item_id,))
        assert cursor.fetchone()[0] is None
    db.commit()

    assert client.get(f"/stock/items/{item_id}").status_code == 200


# ═══ Partners: longer brand and product article ═══


def test_partner_brand_and_product_article_fit_the_real_columns():
    partner = Partner(PartnerSettings("eva", "EVA", True, "", True, "Б" * 128, "", 5), NOW)
    partner.set_product_map([ProductMapping(1, external_id="A" * 255)])

    assert (len(partner.vendor_name), len(partner.product_map[0].external_id)) == (128, 255)
    with pytest.raises(DomainError):
        partner.set_product_map([ProductMapping(1, external_id="A" * 256)])


# ═══ Product order snapshots: rows with their own key ═══


def test_recalculate_all_takes_a_snapshot_that_restores_the_order(api, db):
    client = api(ADMIN)
    with db.cursor() as cursor:
        cursor.execute("SELECT product_id, sort_order FROM dd_product ORDER BY product_id LIMIT 1")
        product_id, sort_order = cursor.fetchone()

    assert client.post("/catalog/sort/recalculate-all").status_code == 200
    [snapshot, *_] = client.get("/catalog/sort/snapshots").json()
    with db.cursor() as cursor:
        cursor.execute("UPDATE dd_product SET sort_order = %s WHERE product_id = %s", (sort_order + 1000, product_id))
    db.commit()

    assert client.post(f"/catalog/sort/snapshots/{snapshot['id']}/restore").status_code == 204
    with db.cursor() as cursor:
        cursor.execute("SELECT sort_order FROM dd_product WHERE product_id = %s", (product_id,))
        restored = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM dd_product_order_snapshot_item WHERE snapshot_id = %s", (snapshot["id"],))
        rows = cursor.fetchone()[0]
    assert (rows > 0, restored) == (True, sort_order)  # the snapshot was taken before the recalculation
