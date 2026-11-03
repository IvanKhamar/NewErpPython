"""Catalog maintenance (item 52): the size filters follow the product sizes."""

from tests import seed
from tests.seed import ADMIN, CATEGORY_PRODUCT_ID, LANGUAGE_ID, MANAGER

SIZE_L = 48
HTMX = {"HX-Request": "true"}


def test_size_filters_are_added_removed_and_created(api, db):
    seed.insert(db, "dd_option_value_description", option_value_id=SIZE_L, language_id=LANGUAGE_ID, option_id=11,
                name="L")  # fmt: skip
    seed.insert(db, "dd_filter", filter_id=300, filter_group_id=3, sort_order=0)
    seed.insert(db, "dd_filter_description", filter_id=300, language_id=LANGUAGE_ID, filter_group_id=3, name="M")
    seed.insert(db, "dd_filter", filter_id=301, filter_group_id=3, sort_order=0)
    seed.insert(db, "dd_filter_description", filter_id=301, language_id=LANGUAGE_ID, filter_group_id=3, name="S")
    seed.insert(db, "dd_product_filter", product_id=CATEGORY_PRODUCT_ID, filter_id=301)  # S, which it no longer has
    seed.option_value(db, 503, 51, CATEGORY_PRODUCT_ID)  # a second size row of the product…
    with db.cursor() as cursor:  # …which is L
        cursor.execute("UPDATE dd_product_option_value SET option_value_id = %s WHERE product_option_value_id = 503",
                       (SIZE_L,))  # fmt: skip

    admin = api(ADMIN)
    assert api(MANAGER).post("/catalog/prices/size-filters").status_code == 403
    result = admin.post("/catalog/prices/size-filters").json()
    # L is new; M and L of this product and M of the seed's product 101 linked; S taken off.
    assert result == {"createdValues": 1, "linked": 3, "unlinked": 1}
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT d.name FROM dd_product_filter pf JOIN dd_filter_description d ON d.filter_id = pf.filter_id "
            "WHERE pf.product_id = %s ORDER BY d.name",
            (CATEGORY_PRODUCT_ID,),
        )
        assert [r[0] for r in cursor.fetchall()] == ["L", "M"]
    assert admin.post("/catalog/prices/size-filters").json() == {"createdValues": 0, "linked": 0, "unlinked": 0}
    assert "Фільтри розмірів" in admin.post("/app/catalog/size-filters", headers=HTMX).text
