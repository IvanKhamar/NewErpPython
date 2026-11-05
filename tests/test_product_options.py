"""Options other than sizes in the product form (item 76): colours and lengths, not mixed up with sizes."""

import pytest

from tests import seed
from tests.seed import LANGUAGE_ID, MANAGER, SIZE_M_OPTION_VALUE_ID, STOREKEEPER

COLOUR, ENGRAVING, LENGTH = 20, 21, 22
RED, BLUE, MIDI = 201, 202, 221
HTMX = {"HX-Request": "true"}


@pytest.fixture(scope="module", autouse=True)
def options(db):
    for option_id, kind, name in ((COLOUR, "select", "Колір"), (ENGRAVING, "text", "Гравіювання"),
                                  (LENGTH, "radio", "Довжина")):  # fmt: skip
        seed.insert(db, "dd_option", option_id=option_id, type=kind, sort_order=option_id)
        seed.insert(db, "dd_option_description", option_id=option_id, language_id=LANGUAGE_ID, name=name)
    seed.insert(db, "dd_option", option_id=11, type="radio", sort_order=0)  # the size option of the common seed
    for value_id, option_id, name in ((RED, COLOUR, "Червоний"), (BLUE, COLOUR, "Синій"), (MIDI, LENGTH, "Міді")):
        seed.insert(db, "dd_option_value", option_value_id=value_id, option_id=option_id, image="", sort_order=value_id)
        seed.insert(db, "dd_option_value_description", option_value_id=value_id, language_id=LANGUAGE_ID,
                    option_id=option_id, name=name)  # fmt: skip


def form(**changes) -> dict:
    return {"model": "OPT-1", "sku": "P", "price": 1000, "name": "Сукня", "image": "", **changes}


def colour(*values: dict, required: bool = True) -> dict:
    return {"optionId": COLOUR, "required": required, "values": list(values)}


def rows(db, product_id: int) -> list[tuple]:
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT product_option_value_id, option_value_id, price, price_prefix, subtract, quantity "
            "FROM dd_product_option_value WHERE product_id = %s ORDER BY option_value_id", (product_id,),
        )  # fmt: skip
        return list(cursor.fetchall())


def parents(db, product_id: int) -> dict[int, tuple]:
    with db.cursor() as cursor:
        cursor.execute("SELECT option_id, value, required FROM dd_product_option WHERE product_id = %s", (product_id,))
        return {row[0]: row[1:] for row in cursor.fetchall()}


def test_options_are_added_changed_and_removed_in_place(api, db):
    client = api(MANAGER)
    created = client.post("/catalog/products", json=form(
        sizes=[{"optionValueId": SIZE_M_OPTION_VALUE_ID, "price": 0}],
        options=[
            colour({"optionValueId": RED, "price": 100}, {"optionValueId": BLUE, "price": 50, "pricePrefix": "-"}),
            {"optionId": ENGRAVING, "required": False, "value": "  Ім'я  "},
        ],
    ))  # fmt: skip
    assert created.status_code == 201, created.text
    product_id = created.json()["productId"]

    product = client.get(f"/catalog/products/{product_id}/edit").json()
    assert [s["optionValueId"] for s in product["sizes"]] == [SIZE_M_OPTION_VALUE_ID]  # not mixed up
    by_id = {o["optionId"]: o for o in product["options"]}
    assert by_id[ENGRAVING]["value"] == "Ім'я" and by_id[ENGRAVING]["required"] is False
    values = {v["optionValueId"]: v for v in by_id[COLOUR]["values"]}
    assert (values[RED]["name"], values[RED]["price"], values[RED]["pricePrefix"]) == ("Червоний", 100, "+")
    assert values[BLUE]["pricePrefix"] == "-"

    first = {row[1]: row for row in rows(db, product_id)}
    assert first[RED][4] == 0 and first[RED][5] == 0  # not counted down, so the storefront shows it at zero stock
    assert first[SIZE_M_OPTION_VALUE_ID][4] == 1

    changed = product | {"options": [colour({"optionValueId": RED, "price": 120, "pricePrefix": "-"})]}
    assert client.put(f"/catalog/products/{product_id}", json=changed).status_code == 200
    after = {row[1]: row for row in rows(db, product_id)}
    assert BLUE not in after
    assert after[RED][0] == first[RED][0]  # the same row: the lines of old orders point to it
    assert (float(after[RED][2]), after[RED][3]) == (120, "-")
    assert ENGRAVING not in parents(db, product_id)  # left out: gone too

    fresh = client.get(f"/catalog/products/{product_id}/edit").json()
    assert client.put(f"/catalog/products/{product_id}", json=fresh | {"options": []}).status_code == 200
    assert COLOUR not in parents(db, product_id)  # no empty list for the storefront to ask about
    assert [row[1] for row in rows(db, product_id)] == [SIZE_M_OPTION_VALUE_ID]


def test_saving_sizes_leaves_other_options_alone(api, db):
    """It used to read a colour as a size: saving refused the product, or dropped the colour with no word."""
    client = api(MANAGER)
    created = client.post("/catalog/products", json=form(options=[colour({"optionValueId": RED})]))
    product_id = created.json()["productId"]
    product = client.get(f"/catalog/products/{product_id}/edit").json()
    assert product["sizes"] == []
    saved = client.put(f"/catalog/products/{product_id}", json=product | {
        "sizes": [{"optionValueId": SIZE_M_OPTION_VALUE_ID, "price": 0}],
    })  # fmt: skip
    assert saved.status_code == 200, saved.text
    assert {row[1] for row in rows(db, product_id)} == {RED, SIZE_M_OPTION_VALUE_ID}


def test_a_value_with_stock_stays(api, db):
    client = api(MANAGER)
    created = client.post("/catalog/products", json=form(options=[colour({"optionValueId": RED})]))
    product_id = created.json()["productId"]
    with db.cursor() as cursor:
        cursor.execute("UPDATE dd_product_option_value SET quantity = 2 WHERE product_id = %s", (product_id,))
    product = client.get(f"/catalog/products/{product_id}/edit").json()
    refused = client.put(f"/catalog/products/{product_id}", json=product | {"options": []})
    assert refused.status_code == 400 and "Червоний" in refused.text
    assert rows(db, product_id)[0][5] == 2

    kept = client.put(f"/catalog/products/{product_id}", json=product)
    assert kept.status_code == 200 and rows(db, product_id)[0][5] == 2  # the form never writes stock


@pytest.mark.parametrize(
    "options",
    [
        [{"optionId": 999, "values": [{"optionValueId": RED}]}],  # no such option
        [{"optionId": 11, "values": [{"optionValueId": SIZE_M_OPTION_VALUE_ID}]}],  # sizes have their own part
        [colour({"optionValueId": MIDI})],  # a value of another option
        [colour({"optionValueId": RED}, {"optionValueId": RED})],
        [colour({"optionValueId": RED}), colour({"optionValueId": BLUE})],
        [colour()],  # a list with nothing to choose
        [colour({"optionValueId": RED, "price": -5})],
        [colour({"optionValueId": RED, "pricePrefix": "*"})],
        [{"optionId": ENGRAVING, "values": [{"optionValueId": RED}]}],  # a text option has no list
        [{"optionId": ENGRAVING, "value": "<script>alert(1)</script>"}],
    ],
)
def test_bad_options_are_refused(api, options):
    response = api(MANAGER).post("/catalog/products", json=form(options=options))
    assert response.status_code in (400, 422), response.text


def test_a_copy_has_the_options_without_stock(api, db):
    client = api(MANAGER)
    source = client.post("/catalog/products", json=form(options=[colour({"optionValueId": BLUE, "price": 70})]))
    source_id = source.json()["productId"]
    with db.cursor() as cursor:
        cursor.execute("UPDATE dd_product_option_value SET quantity = 5 WHERE product_id = %s", (source_id,))
    copy_id = client.post(f"/catalog/products/{source_id}/copy").json()["productId"]
    [value] = client.get(f"/catalog/products/{copy_id}/edit").json()["options"][0]["values"]
    assert (value["optionValueId"], value["price"], value["quantity"]) == (BLUE, 70, 0)


def test_lookups_leave_sizes_out_and_the_form_is_closed_to_other_roles(api):
    lookups = api(MANAGER).get("/catalog/products/form-lookups").json()
    by_id = {o["id"]: o for o in lookups["options"]}
    assert set(by_id) == {COLOUR, ENGRAVING, LENGTH}
    assert by_id[COLOUR]["hasValues"] is True and [v["name"] for v in by_id[COLOUR]["values"]] == ["Червоний", "Синій"]
    assert by_id[ENGRAVING]["hasValues"] is False
    assert api(STOREKEEPER).post("/catalog/products", json=form()).status_code == 403


def test_the_web_form_saves_options(api, db):
    client = api(MANAGER)
    product_id = client.post("/catalog/products", json=form()).json()["productId"]
    page = client.get(f"/app/catalog/products/{product_id}/edit").text
    assert 'name="option_value_20"' in page and "Гравіювання" in page
    stamp = client.get(f"/catalog/products/{product_id}/edit").json()["dateModified"]

    saved = client.post(f"/app/catalog/products/{product_id}/edit", headers=HTMX, data={
        "name": "Сукня", "model": "OPT-1", "price": "1000", "date_modified": stamp,
        "option_value_20": [str(RED)], "option_required_20": "1", f"value_price_{RED}": "99,50",
        f"value_prefix_{RED}": "-", "option_text": str(ENGRAVING), "option_text_value_21": "Лев",
        f"value_price_{MIDI}": "5",  # a price with its value not ticked means nothing
    })  # fmt: skip
    assert "Товар збережено." in saved.text, saved.text
    [row] = rows(db, product_id)
    assert (row[1], float(row[2]), row[3]) == (RED, 99.5, "-")
    assert parents(db, product_id)[ENGRAVING] == ("Лев", 0)
    assert parents(db, product_id)[COLOUR][1] == 1
