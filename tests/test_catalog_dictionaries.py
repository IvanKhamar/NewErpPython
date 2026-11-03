"""Catalog dictionaries (items 47, 48): labels, storefront search presets, categories with their tree paths."""

import pytest

from tests.seed import ADMIN, MANAGER, STOREKEEPER

HTMX = {"HX-Request": "true"}


def test_labels_are_created_checked_and_taken_off_products_when_deleted(api, db):
    client = api(MANAGER)
    created = client.post(
        "/catalog/labels", json={"name": " Хіт ", "backgroundColor": "#ff0000", "textColor": "#FFFFFF"}
    )
    assert created.status_code == 201
    label = created.json()
    assert (label["name"], label["enabled"], label["products"]) == ("Хіт", True, 0)

    bad = {"name": "X", "backgroundColor": "red;background:url(x)"}
    assert client.post("/catalog/labels", json=bad).status_code == 400
    assert client.post("/catalog/labels", json={"name": " "}).status_code == 400
    assert client.put(f"/catalog/labels/{label['id']}", json=label | {"sortOrder": 5}).json()["sortOrder"] == 5
    assert client.put("/catalog/labels/9999", json=label).status_code == 404

    with db.cursor() as cursor:
        cursor.execute("INSERT INTO dd_product_to_label (product_id, label_id) VALUES (102, %s)", (label["id"],))
    assert next(x for x in client.get("/catalog/labels").json() if x["id"] == label["id"])["products"] == 1
    assert client.delete(f"/catalog/labels/{label['id']}").status_code == 204
    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM dd_product_to_label WHERE label_id = %s", (label["id"],))
        assert cursor.fetchone()[0] == 0
    assert client.delete(f"/catalog/labels/{label['id']}").status_code == 404


@pytest.mark.parametrize(
    ("url", "status"),
    [
        ("/catalog/sukni?filter=new", 201),
        ("https://vidro.example/sale", 201),
        ("javascript:alert(1)", 400),
        ("//evil.example/x", 400),
        ('/x" onmouseover="a', 400),
        ("data:text/html,x", 400),
    ],
)
def test_search_preset_addresses_are_only_the_shop_or_https(api, url, status):
    response = api(MANAGER).post("/catalog/search-presets", json={"name": "Новинки", "url": url})
    assert response.status_code == status


def test_search_presets_are_edited_and_deleted(api):
    client = api(MANAGER)
    preset = client.post("/catalog/search-presets", json={"name": "Літо", "url": "/summer", "sortOrder": 2}).json()
    changed = client.put(f"/catalog/search-presets/{preset['id']}", json=preset | {"enabled": False}).json()
    assert (changed["enabled"], changed["url"]) == (False, "/summer")
    assert client.delete(f"/catalog/search-presets/{preset['id']}").status_code == 204
    assert client.delete(f"/catalog/search-presets/{preset['id']}").status_code == 404


def category(name: str, parent_id: int = 0, **changes) -> dict:
    return {"name": name, "parentId": parent_id, **changes}


def paths(db, category_id: int) -> list[tuple[int, int]]:
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT path_id, level FROM dd_category_path WHERE category_id = %s ORDER BY level", (category_id,)
        )
        return list(cursor.fetchall())


def test_categories_keep_their_tree_paths_and_refuse_loops(api, db):
    client = api(MANAGER)
    clothes = client.post("/catalog/categories", json=category("Одяг", seoKeyword="odiah")).json()
    dresses = client.post(
        "/catalog/categories",
        json=category("Сукні", clothes["id"], description="<p>Лляні<script>x()</script></p>", productionPeriod=14),
    ).json()
    assert (dresses["description"], dresses["productionPeriod"]) == ("<p>Лляні</p>", 14)
    summer = client.post("/catalog/categories", json=category("Літні", dresses["id"])).json()
    assert paths(db, summer["id"]) == [(clothes["id"], 0), (dresses["id"], 1), (summer["id"], 2)]
    names = {c["id"]: c["name"] for c in client.get("/catalog/categories").json()}
    assert names[summer["id"]] == "Одяг > Сукні > Літні"

    # Moving a branch rebuilds the paths below it too.
    top = client.put(f"/catalog/categories/{dresses['id']}", json=dresses | {"parentId": 0}).json()
    assert top["parentId"] == 0
    assert paths(db, summer["id"]) == [(dresses["id"], 0), (summer["id"], 1)]

    loop = client.put(f"/catalog/categories/{dresses['id']}", json=dresses | {"parentId": summer["id"]})
    assert loop.status_code == 400
    assert client.post("/catalog/categories", json=category("Дубль", seoKeyword="odiah")).status_code == 409
    assert client.post("/catalog/categories", json=category("Х", 9999)).status_code == 400
    assert client.post("/catalog/categories", json=category(" ")).status_code == 400
    assert client.get("/catalog/categories/9999").status_code == 404


def test_only_the_admin_deletes_categories_and_not_with_subcategories(api, db):
    manager, admin = api(MANAGER), api(ADMIN)
    parent = manager.post("/catalog/categories", json=category("Аксесуари")).json()
    child = manager.post("/catalog/categories", json=category("Сумки", parent["id"])).json()

    assert manager.delete(f"/catalog/categories/{child['id']}").status_code == 403
    assert admin.delete(f"/catalog/categories/{parent['id']}").status_code == 400
    assert admin.delete(f"/catalog/categories/{child['id']}").status_code == 204
    assert admin.delete(f"/catalog/categories/{parent['id']}").status_code == 204
    assert paths(db, parent["id"]) == []

    with db.cursor() as cursor:
        cursor.execute("DELETE FROM dd_category_path")  # a broken tree
    assert admin.post("/catalog/categories/repair").json()["categories"] >= 1
    assert manager.post("/catalog/categories/repair").status_code == 403
    assert api(STOREKEEPER).get("/catalog/categories").status_code == 403


def test_web_dictionary_pages(api):
    client = api(ADMIN)
    page = client.get("/app/catalog/labels")
    assert page.status_code == 200 and 'hx-post="/app/catalog/labels/label/0/save"' in page.text
    added = client.post("/app/catalog/labels/label/0/save", data={"name": "Веб", "background_color": "#00ff00",
                                                                  "enabled": "1"}, headers=HTMX)  # fmt: skip
    assert "Збережено." in added.text and 'value="Веб"' in added.text
    refused = client.post("/app/catalog/labels/preset/0/save", data={"name": "X", "url": "javascript:x"}, headers=HTMX)
    assert 'class="error"' in refused.text

    assert "Нова категорія" in client.get("/app/catalog/categories").text
    created = client.post("/app/catalog/categories/0", data={"name": "Веб-категорія", "enabled": "1"}, headers=HTMX)
    assert created.status_code == 204
    url = created.headers["HX-Redirect"]
    category_id = int(url.rsplit("/", 1)[1])
    form = client.get(url)
    assert "Видалити категорію" in form.text and "Веб-категорія" in form.text
    saved = client.post(f"/app/catalog/categories/{category_id}", data={"name": "Веб 2", "description": "<b>x</b>"},
                        headers=HTMX)  # fmt: skip
    assert "Категорію збережено." in saved.text
    loop = client.post(f"/app/catalog/categories/{category_id}", data={"name": "Веб 2", "parent_id": str(category_id)},
                       headers=HTMX)  # fmt: skip
    assert 'class="error"' in loop.text
    assert "Дерево перебудовано" in client.post("/app/catalog/categories/repair", headers=HTMX).text
    assert api(MANAGER).post(f"/app/catalog/categories/{category_id}/delete", headers=HTMX).status_code == 403
    assert client.post(f"/app/catalog/categories/{category_id}/delete", headers=HTMX).headers["HX-Redirect"] == (
        "/app/catalog/categories"
    )
