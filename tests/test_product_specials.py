"""Sale prices (dd_product_special) in the product search of a new order. Its own database: the test adds specials."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from newerp.domain.catalog import SpecialPrice
from tests import helpers
from tests.seed import CATEGORY_PRODUCT_ID, MANAGER, insert

HTMX = {"HX-Request": "true"}


@pytest.mark.parametrize(
    ("regular", "percent", "expected"),
    [(900, 15, 770), (900, 10, 810), (1250, 33, 840), (95, 50, 50), (100, 100, 0)],  # 765 → 770, 837.5 → 840
)
def test_percent_off_is_rounded_to_tens_like_the_old_site(regular, percent, expected):
    assert SpecialPrice(Decimal(0), Decimal(percent)).apply(Decimal(regular)) == expected


def test_fixed_special_price_is_used_without_a_percent():
    assert SpecialPrice(Decimal("799.00"), Decimal(0)).apply(Decimal(900)) == Decimal("799.00")


def test_search_gives_the_first_active_special_and_keeps_the_regular_price(api, db):
    today = helpers.today()

    def special(priority: int, price: int, percent: int, start: date, end: date) -> None:
        insert(db, "dd_product_special", product_id=CATEGORY_PRODUCT_ID, customer_group_id=1, priority=priority,
               price=price, percent=percent, date_start=start.isoformat(), date_end=end.isoformat())  # fmt: skip

    special(1, 500, 0, today - timedelta(days=30), today)  # ended today: the end day no longer counts
    special(1, 650, 0, today + timedelta(days=1), today + timedelta(days=9))  # not started yet
    special(2, 0, 15, today, today + timedelta(days=7))  # started today: on
    special(3, 700, 0, today - timedelta(days=1), today + timedelta(days=7))  # lower priority

    client = api(MANAGER)
    [product] = client.get("/orders/new/products?search=лляна").json()
    assert (product["price"], product["regularPrice"]) == (770, 900)
    found = client.get("/app/orders/new/products?search=лляна", headers=HTMX).text
    assert "<del>900,00</del>" in found
    assert "акція 770,00" in found
    assert "price=770" in found  # the size button puts the sale price into the line


def test_special_without_dates_is_always_on(api, db):
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM dd_product_special")
        # The old site writes 0000-00-00 for "no limit"; a session without strict dates can store it, as it did.
        cursor.execute("SET SESSION sql_mode = ''")
        cursor.execute(
            "INSERT INTO dd_product_special (product_id, customer_group_id, price, date_start, date_end) "
            "VALUES (%s, 1, 799, '0000-00-00', '0000-00-00')",
            (CATEGORY_PRODUCT_ID,),
        )
    [product] = api(MANAGER).get("/orders/new/products?search=лляна").json()
    assert (product["price"], product["regularPrice"]) == (799, 900)
