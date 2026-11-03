"""Filters, attributes, options, manufacturers and downloads (item 49), and attributes and downloads in the product
form."""

import pytest

from tests.seed import ADMIN, MANAGER, STOREKEEPER

HTMX = {"HX-Request": "true"}


@pytest.mark.parametrize("kind", ["filters", "attributes", "options"])
def test_groups_and_values_are_edited_and_used_ones_kept(api, kind):
    client = api(MANAGER)
    group = client.post(f"/catalog/references/{kind}", json={"name": " Колір ", "sortOrder": 1, "type": "radio"})
    assert group.status_code == 201, group.text
    group = group.json()
    assert (group["name"], group["items"]) == ("Колір", [])
    if kind == "options":
        assert group["type"] == "radio"

    with_value = client.post(f"/catalog/references/{kind}/{group['id']}/values", json={"name": "Червоний"}).json()
    [value] = with_value["items"]
    renamed = client.put(f"/catalog/references/{kind}/{group['id']}/values/{value['id']}",
                         json={"name": "Бордо", "sortOrder": 2}).json()  # fmt: skip
    assert [(i["name"], i["sortOrder"], i["products"]) for i in renamed["items"]] == [("Бордо", 2, 0)]
    assert client.put(f"/catalog/references/{kind}/{group['id']}/values/99999", json={"name": "x"}).status_code == 404
    assert client.post(f"/catalog/references/{kind}/{group['id']}/values", json={"name": " "}).status_code == 400

    assert client.delete(f"/catalog/references/{kind}/values/{value['id']}").status_code == 204
    assert client.delete(f"/catalog/references/{kind}/{group['id']}").status_code == 204
    assert all(g["id"] != group["id"] for g in client.get(f"/catalog/references/{kind}").json())
    assert client.get("/catalog/references/colors").status_code == 400  # not a kind


def test_a_value_on_a_product_cannot_be_deleted_and_shows_in_the_product_form(api):
    client = api(MANAGER)
    group = client.post("/catalog/references/attributes", json={"name": "Склад"}).json()
    attribute = client.post(f"/catalog/references/attributes/{group['id']}/values", json={"name": "Тканина"}).json()
    attribute_id = attribute["items"][0]["id"]
    download = client.post("/catalog/downloads", json={"name": "Догляд", "filename": "care.pdf"}).json()
    assert download["mask"] == "care.pdf"

    lookups = client.get("/catalog/products/form-lookups").json()
    assert {"id": attribute_id, "name": "Склад: Тканина"} in lookups["attributes"]
    twice = [{"attributeId": attribute_id, "text": "Льон 100%"}, {"attributeId": attribute_id, "text": "x"}]
    body = {"name": "Сукня з атрибутом", "model": "ATTR-1", "downloadIds": [download["id"]], "attributes": twice}
    product_id = client.post("/catalog/products", json=body).json()["productId"]
    product = client.get(f"/catalog/products/{product_id}/edit").json()
    assert product["attributes"] == [{"attributeId": attribute_id, "text": "x"}]  # once per attribute: the last
    assert product["downloadIds"] == [download["id"]]
    assert (
        client.post("/catalog/products", json=body | {"attributes": [{"attributeId": 99999, "text": "x"}]}).status_code
        == 400
    )

    refused = client.delete(f"/catalog/references/attributes/values/{attribute_id}")
    assert refused.status_code == 400 and "1 товарах" in refused.json()["detail"]
    assert client.delete(f"/catalog/references/attributes/{group['id']}").status_code == 400
    assert client.delete(f"/catalog/downloads/{download['id']}").status_code == 400


def test_manufacturers_and_downloads(api, db):
    client = api(MANAGER)
    maker = client.post("/catalog/manufacturers", json={"name": "Vidro", "image": "catalog/brands/vidro.png"})
    assert maker.status_code == 201
    maker = maker.json()
    assert client.post("/catalog/manufacturers", json={"name": "X", "image": "../x.png"}).status_code == 400
    assert client.put(f"/catalog/manufacturers/{maker['id']}", json=maker | {"sortOrder": 3}).json()["sortOrder"] == 3
    with db.cursor() as cursor:
        cursor.execute("UPDATE dd_product SET manufacturer_id = %s WHERE product_id = 102", (maker["id"],))
    assert client.delete(f"/catalog/manufacturers/{maker['id']}").status_code == 400
    with db.cursor() as cursor:
        cursor.execute("UPDATE dd_product SET manufacturer_id = 0 WHERE product_id = 102")
    assert client.delete(f"/catalog/manufacturers/{maker['id']}").status_code == 204

    for filename in ("../etc/passwd", "a/b.pdf", ""):
        assert client.post("/catalog/downloads", json={"name": "Файл", "filename": filename}).status_code == 400
    assert api(STOREKEEPER).get("/catalog/manufacturers").status_code == 403
    assert api(ADMIN).get("/catalog/downloads").status_code == 200


def test_web_reference_pages_and_product_attributes(api):
    client = api(MANAGER)
    for tab in ("filters", "attributes", "options", "manufacturers", "downloads"):
        assert client.get(f"/app/catalog/references?tab={tab}").status_code == 200
    group = client.post("/app/catalog/references/options/0/0/save", data={"name": "Довжина", "type": "radio"},
                        headers=HTMX)  # fmt: skip
    assert "Збережено." in group.text and 'value="Довжина"' in group.text
    [options] = [g for g in client.get("/catalog/references/options").json() if g["name"] == "Довжина"]
    value = client.post(f"/app/catalog/references/options/{options['id']}/0/save",
                        data={"name": "Міді", "level": "value"}, headers=HTMX)  # fmt: skip
    assert 'value="Міді"' in value.text
    bad = client.post(
        "/app/catalog/references/downloads/0/0/save", data={"name": "X", "filename": "../x"}, headers=HTMX
    )
    assert 'class="error"' in bad.text

    attributes = client.post("/catalog/references/attributes", json={"name": "Догляд"}).json()
    attribute_id = client.post(f"/catalog/references/attributes/{attributes['id']}/values",
                               json={"name": "Прання"}).json()["items"][0]["id"]  # fmt: skip
    form = client.get("/app/catalog/products/new")
    assert f'name="attr_{attribute_id}"' in form.text
    created = client.post("/app/catalog/products/new", headers=HTMX,
                          data={"name": "З атрибутом", "model": "WEB-ATTR", f"attr_{attribute_id}": "30°"})  # fmt: skip
    product_id = int(created.headers["HX-Redirect"].split("/")[-2])
    assert client.get(f"/catalog/products/{product_id}/edit").json()["attributes"] == [
        {"attributeId": attribute_id, "text": "30°"}
    ]
