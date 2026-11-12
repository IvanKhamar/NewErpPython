"""The product form of the old admin (items 43, 45): create, edit, copy, sizes, links, and "remove NEW"."""

import pytest

from tests import seed
from tests.seed import CATEGORY_ID, CATEGORY_PRODUCT_ID, LANGUAGE_ID, MANAGER, SIZE_M_OPTION_VALUE_ID, STOREKEEPER

SIZE_L = 48
NEW_LABEL_ID, HIT_LABEL_ID = 1, 2
NEW_FILTER_ID = 70


@pytest.fixture(scope="module", autouse=True)
def catalog(db):
    seed.insert(db, "dd_category", category_id=CATEGORY_ID, parent_id=0, sort_order=0, status=True, top=False,
                column=0, date_added="2026-01-01", date_modified="2026-01-01")  # fmt: skip
    seed.insert(
        db, "dd_category_description", category_id=CATEGORY_ID, language_id=LANGUAGE_ID, name="Сорочки",
        description="", meta_title="", meta_description="", meta_keyword="",
    )  # fmt: skip
    seed.insert(db, "dd_product_label", label_id=NEW_LABEL_ID, name="NEW", sort_order=0, status=True)
    seed.insert(db, "dd_product_label", label_id=HIT_LABEL_ID, name="Хіт", sort_order=1, status=True)
    seed.insert(db, "dd_filter", filter_id=NEW_FILTER_ID, filter_group_id=7, sort_order=0)
    seed.insert(db, "dd_filter_description", filter_id=NEW_FILTER_ID, language_id=LANGUAGE_ID, filter_group_id=7,
                name="NEW")  # fmt: skip
    for value_id, name in ((SIZE_M_OPTION_VALUE_ID, "M"), (SIZE_L, "L")):
        if value_id != SIZE_M_OPTION_VALUE_ID:  # M is in the common seed
            seed.insert(db, "dd_option_value_description", option_value_id=value_id, language_id=LANGUAGE_ID,
                        option_id=11, name=name)  # fmt: skip


def form(**changes) -> dict:
    return {
        "model": "DR-1",
        "sku": "PRINT-1",
        "price": 1200,
        "name": "Сукня «Лев»",
        "description": '<p onclick="steal()">Лляна сукня<script>alert(1)</script></p><a href="javascript:x()">x</a>',
        "tableSize": "<table><tr><td>S</td></tr></table>",
        "tag": "літо,тема:NEW",
        "image": "catalog/dresses/lion.jpg",
        "seoKeyword": "suknia-lev",
        "categoryIds": [CATEGORY_ID],
        "labelIds": [NEW_LABEL_ID],
        "filterIds": [NEW_FILTER_ID],
        "images": [{"image": "catalog/dresses/lion-2.jpg", "sortOrder": 1}],
        "specials": [{"customerGroupId": 1, "priority": 1, "price": 999}],
        "sizes": [{"optionValueId": SIZE_M_OPTION_VALUE_ID, "price": 0}, {"optionValueId": SIZE_L, "price": 1300}],
        **changes,
    }


def test_create_cleans_html_and_saves_every_part(api):
    client = api(MANAGER)
    response = client.post("/catalog/products", json=form())
    assert response.status_code == 201, response.text
    product_id = response.json()["productId"]
    assert response.headers["Location"] == f"/catalog/products/{product_id}"

    product = client.get(f"/catalog/products/{product_id}/edit").json()
    assert product["description"] == '<p>Лляна сукня</p><a rel="noopener noreferrer">x</a>'
    assert product["tableSize"] == "<table><tbody><tr><td>S</td></tr></tbody></table>"
    assert (product["enabled"], product["seoKeyword"]) == (False, "suknia-lev")
    assert product["image"] == "catalog/dresses/lion.jpg"
    assert (product["categoryIds"], product["labelIds"], product["filterIds"]) == ([CATEGORY_ID], [1], [NEW_FILTER_ID])
    assert [(s["optionValueId"], s["name"], s["price"], s["quantity"]) for s in product["sizes"]] == [
        (SIZE_M_OPTION_VALUE_ID, "M", 0, 0),
        (SIZE_L, "L", 1300, 0),
    ]
    [special] = product["specials"]
    assert (special["price"], special["dateStart"], special["dateEnd"]) == (999, None, None)  # no limit both ways
    # The sale price works in the product search of a new order (it finds enabled products only).
    enabled = client.post("/catalog/products", json=form(model="DR-SALE", seoKeyword="", enabled=True)).json()
    [found] = client.get("/orders/new/products?search=DR-SALE").json()
    assert found["id"] == enabled["productId"]
    assert (found["price"], found["regularPrice"]) == (999, 1200)


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({"name": " "}, 400),
        ({"image": "../../config.php"}, 400),
        ({"image": "catalog/a.php"}, 400),
        ({"seoKeyword": "Сукня лев"}, 400),
        ({"categoryIds": [999]}, 400),
        ({"sizes": [{"optionValueId": 999}]}, 400),
        ({"relatedIds": [999999]}, 400),
        ({"model": "x" * 65}, 400),
        ({"modelInfo": "x" * 256}, 400),  # the column is varchar(255)
    ],
)
def test_create_refuses_bad_values(api, changes, status):
    assert api(MANAGER).post("/catalog/products", json=form(**changes)).status_code == status


def test_seo_address_must_be_unique(api):
    client = api(MANAGER)
    assert client.post("/catalog/products", json=form(seoKeyword="unique-one")).status_code == 201
    assert client.post("/catalog/products", json=form(seoKeyword="unique-one")).status_code == 409


def test_save_checks_the_version_keeps_stock_and_links_colours_both_ways(api, db):
    client = api(MANAGER)
    first = client.post("/catalog/products", json=form(seoKeyword="")).json()["productId"]
    second = client.post("/catalog/products", json=form(seoKeyword="", sizes=[])).json()["productId"]
    with db.cursor() as cursor:  # production put stock on size M
        cursor.execute("UPDATE dd_product_option_value SET quantity = 3 WHERE product_id = %s AND option_value_id = %s",
                       (first, SIZE_M_OPTION_VALUE_ID))  # fmt: skip
        # OpenCart keeps whole seconds: an older version, so this test can tell two saves apart.
        cursor.execute("UPDATE dd_product SET date_modified = '2026-01-01' WHERE product_id = %s", (first,))

    product = client.get(f"/catalog/products/{first}/edit").json()
    changed = product | {"price": 1500, "relatedColorIds": [second], "labelIds": [HIT_LABEL_ID]}
    changed["sizes"] = [{"optionValueId": SIZE_M_OPTION_VALUE_ID, "price": 1550}]  # L is removed
    saved = client.put(f"/catalog/products/{first}", json=changed)
    assert saved.status_code == 200, saved.text
    assert [(s["name"], s["price"], s["quantity"]) for s in saved.json()["sizes"]] == [("M", 1550, 3)]
    assert client.get(f"/catalog/products/{second}/edit").json()["relatedColorIds"] == [first]

    # The form opened before the save is stale now.
    assert client.put(f"/catalog/products/{first}", json=changed).status_code == 409
    fresh = client.get(f"/catalog/products/{first}/edit").json()
    no_m = fresh | {"sizes": []}
    refused = client.put(f"/catalog/products/{first}", json=no_m)
    assert refused.status_code == 400 and "3 шт." in refused.json()["detail"]
    assert client.put(f"/catalog/products/{first}", json=fresh | {"dateModified": None}).status_code == 400
    assert client.put("/catalog/products/999999", json=fresh).status_code == 404


def test_copy_is_disabled_without_stock_and_seo_address(api, db):
    client = api(MANAGER)
    source = client.post("/catalog/products", json=form(seoKeyword="to-copy", enabled=True)).json()["productId"]
    with db.cursor() as cursor:
        cursor.execute("UPDATE dd_product_option_value SET quantity = 4 WHERE product_id = %s", (source,))

    copied = client.post(f"/catalog/products/{source}/copy")
    assert copied.status_code == 201
    copy = client.get(f"/catalog/products/{copied.json()['productId']}/edit").json()
    assert (copy["name"], copy["enabled"], copy["seoKeyword"]) == ("Сукня «Лев»", False, "")
    assert {s["quantity"] for s in copy["sizes"]} == {0}
    assert copy["specials"][0]["price"] == 999


def test_remove_new_takes_the_label_filter_and_tag_off_a_print(api):
    client = api(MANAGER)
    print_form = form(sku="PRINT-9", seoKeyword="")
    ids = [client.post("/catalog/products", json=print_form).json()["productId"] for _ in range(2)]

    result = client.post("/catalog/products/remove-new", json={"sku": "PRINT-9"}).json()
    assert (sorted(result["productIds"]), result["labelRemoved"], result["filterRemoved"]) == (sorted(ids), True, True)
    product = client.get(f"/catalog/products/{ids[0]}/edit").json()
    assert (product["labelIds"], product["filterIds"], product["tag"]) == ([], [], "літо")
    assert client.post("/catalog/products/remove-new", json={"sku": "NOPE"}).status_code == 404
    assert client.post("/catalog/products/remove-new", json={"sku": " "}).status_code == 400


def test_form_lookups_and_access(api):
    lookups = api(MANAGER).get("/catalog/products/form-lookups").json()
    assert [s["name"] for s in lookups["sizes"]] == ["M", "L"]
    assert {"id": NEW_FILTER_ID, "name": "7: NEW"} in lookups["filters"]
    assert api(STOREKEEPER).post("/catalog/products", json=form()).status_code == 403
    assert api(MANAGER).get(f"/catalog/products/{CATEGORY_PRODUCT_ID}/edit").status_code == 200


HTMX = {"HX-Request": "true"}


def web_form(**changes) -> dict:
    fields = {
        "name": "Сорочка «Веб»", "model": "WEB-1", "sku": "WEB", "price": "900,50", "minimum": "1", "sort_order": "0",
        "manufacturer_id": "0", "stock_status_id": "0", "weight": "300", "length": "0", "width": "0", "height": "0",
        "description": "<p>Опис</p><img src=x onerror=alert(1)>", "seo_keyword": "", "image": "",
        "images": "catalog/web/1.jpg\ncatalog/web/2.jpg", "category_ids": [str(CATEGORY_ID)], "size": [str(SIZE_L)],
        f"size_price_{SIZE_L}": "950", "special_group": ["1", "1"], "special_priority": ["1", "1"],
        "special_price": ["800", ""], "special_percent": ["", ""], "special_start": ["2026-11-01", ""],
        "special_end": ["", ""], "related_ids": "", "related_color_ids": "", "related_variant_ids": "",
    }  # fmt: skip
    return fields | changes


def test_web_product_form_creates_and_saves(api):
    client = api(MANAGER)
    assert 'name="seo_keyword"' in client.get("/app/catalog/products/new").text

    created = client.post("/app/catalog/products/new", data=web_form(), headers=HTMX)
    assert created.status_code == 204, created.text
    edit_url = created.headers["HX-Redirect"]
    product_id = int(edit_url.split("/")[-2])
    product = client.get(f"/catalog/products/{product_id}/edit").json()
    assert (product["price"], product["description"]) == (900.5, '<p>Опис</p><img src="x">')
    assert [i["image"] for i in product["images"]] == ["catalog/web/1.jpg", "catalog/web/2.jpg"]
    assert [(s["price"], s["dateStart"]) for s in product["specials"]] == [(800, "2026-11-01")]

    page = client.get(edit_url)
    assert page.status_code == 200 and "Копіювати товар" in page.text
    data = web_form(name="Сорочка «Веб» 2", date_modified=product["dateModified"], related_ids="1, x")
    refused = client.post(edit_url, data=data, headers=HTMX)
    assert "Номери товарів через кому" in refused.text
    saved = client.post(edit_url, data=data | {"related_ids": str(CATEGORY_PRODUCT_ID)}, headers=HTMX)
    assert "Товар збережено." in saved.text and saved.headers["HX-Retarget"] == "#product-form"

    copied = client.post(f"/app/catalog/products/{product_id}/copy", headers=HTMX)
    assert copied.status_code == 204 and copied.headers["HX-Redirect"].endswith("/edit")
    removed = client.post("/app/catalog/products/remove-new", data={"sku": "WEB"}, headers=HTMX)
    assert "NEW знято" in removed.text
    assert client.get("/app/catalog/products/999999/edit").status_code == 404
