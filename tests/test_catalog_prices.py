"""Mass price changes (item 46) and the CSV import of products (item 44): preview first, then apply."""

import pytest

from tests import seed
from tests.seed import ADMIN, LANGUAGE_ID, MANAGER

DRESSES_ID, OTHER_CATEGORY_ID = 6, 7  # not the common seed's category, which has a product too
HTMX = {"HX-Request": "true"}


@pytest.fixture(scope="module")
def products(api, db) -> list[int]:
    for category_id, name in ((DRESSES_ID, "Сукні"), (OTHER_CATEGORY_ID, "Сорочки")):
        seed.insert(db, "dd_category", category_id=category_id, parent_id=0, sort_order=0, status=True, top=False,
                    column=0, date_added="2026-01-01", date_modified="2026-01-01")  # fmt: skip
        seed.insert(db, "dd_category_description", category_id=category_id, language_id=LANGUAGE_ID, name=name,
                    description="", meta_title="", meta_description="", meta_keyword="")  # fmt: skip
    client = api(MANAGER)
    ids = []
    for model, price, category_id in (("PR-1", 1000, DRESSES_ID), ("PR-2", 1490, DRESSES_ID),
                                      ("PR-3", 700, OTHER_CATEGORY_ID)):  # fmt: skip
        body = {"name": f"Товар {model}", "model": model, "price": price, "categoryIds": [category_id]}
        ids.append(client.post("/catalog/products", json=body).json()["productId"])
    return ids


def test_price_change_previews_then_applies_only_the_chosen_category(api, products):
    admin = api(ADMIN)
    body = {"target": "price", "operation": "add", "amount": 10, "unit": "percent", "categoryIds": [DRESSES_ID]}
    preview = admin.post("/catalog/prices/preview", json=body).json()
    assert (preview["total"], preview["changed"]) == (2, 2)
    assert [(r["old"], r["new"]) for r in preview["rows"]] == [(1000, 1100), (1490, 1639)]  # whole hryvnias
    assert admin.get(f"/catalog/products/{products[0]}/edit").json()["price"] == 1000  # nothing written yet

    assert admin.post("/catalog/prices/apply", json=body).json() == {"changed": 2}
    assert admin.get(f"/catalog/products/{products[1]}/edit").json()["price"] == 1639
    assert admin.get(f"/catalog/products/{products[2]}/edit").json()["price"] == 700  # other category


def test_sale_percent_creates_or_updates_sales_and_remove_takes_them_off(api, products):
    admin = api(ADMIN)
    body = {"target": "sale", "operation": "set_percent", "amount": 20, "categoryIds": [OTHER_CATEGORY_ID]}
    assert admin.post("/catalog/prices/apply", json=body).json() == {"changed": 1}
    [special] = admin.get(f"/catalog/products/{products[2]}/edit").json()["specials"]
    assert (special["percent"], special["price"], special["dateEnd"]) == (20, 0, None)
    # Applying the same again changes nothing.
    assert admin.post("/catalog/prices/apply", json=body).json() == {"changed": 0}

    remove = {"target": "sale", "operation": "remove", "categoryIds": [OTHER_CATEGORY_ID]}
    assert admin.post("/catalog/prices/apply", json=remove).json() == {"changed": 1}
    assert admin.get(f"/catalog/products/{products[2]}/edit").json()["specials"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"target": "price", "operation": "add", "amount": 10},  # no category and no "all products"
        {"target": "price", "operation": "set_percent", "amount": 10, "allProducts": True},
        {"target": "sale", "operation": "set_percent", "amount": 100, "allProducts": True},
        {"target": "price", "operation": "divide", "amount": 0, "allProducts": True},
    ],
)
def test_price_change_refuses_unclear_requests(api, products, body):
    assert api(ADMIN).post("/catalog/prices/preview", json=body).status_code == 400


def test_bulk_changes_are_for_the_admin_only(api, products):
    manager = api(MANAGER)
    assert manager.post("/catalog/prices/preview", json={"allProducts": True}).status_code == 403
    assert manager.post("/catalog/products/import", files={"file": ("a.csv", b"model;price\n")}).status_code == 403


def upload(client, path: str, text: str):
    return client.post(path, files={"file": ("products.csv", text.encode("utf-8-sig"), "text/csv")})


def test_csv_import_updates_by_model_or_id_and_reports_bad_rows(api, products):
    admin = api(ADMIN)
    csv = (
        "model;price;status;name;sku\n"
        "PR-1;1250,50;0;Нова назва;\n"  # an empty cell keeps the value
        "PR-3;;;;SKU-3\n"
        "NOPE;100;;;\n"
        "PR-2;дорого;;;\n"
    )
    preview = upload(admin, "/catalog/products/import/preview", csv).json()
    assert (preview["errors"], preview["unchanged"]) == (2, 0)
    first, third, missing, bad = preview["rows"]
    assert first["changes"] == {"price": "1100.0000 → 1250.50", "name": "Товар PR-1 → Нова назва"}  # status was 0
    assert third["changes"] == {"sku": " → SKU-3"}
    assert "імпорт лише оновлює" in missing["error"] and "не число" in bad["error"]

    assert upload(admin, "/catalog/products/import", csv).json() == {"updated": 2, "errors": 2}
    product = admin.get(f"/catalog/products/{products[0]}/edit").json()
    assert (product["price"], product["name"], product["enabled"]) == (1250.5, "Нова назва", False)
    assert admin.get(f"/catalog/products/{products[2]}/edit").json()["sku"] == "SKU-3"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"price;color\n1;red\n", "Невідомі колонки"),
        (b"price\n1\n", "model або product_id"),
        ("model;price\nСукня;1\n".encode("cp1251"), "UTF-8"),
        (b"", "порожній"),
        (b"model;price\n" + b"X;1\n" * 5001, "5000"),
        (b"x" * 1_000_001, "МБ"),
    ],
)
def test_csv_import_refuses_bad_files(api, products, content, message):
    response = api(ADMIN).post("/catalog/products/import/preview", files={"file": ("a.csv", content)})
    assert response.status_code == 400 and message in response.json()["detail"]


def test_web_quick_price_mass_prices_and_import(api, products):
    admin = api(ADMIN)
    listing = admin.get("/app/catalog/products?search=PR-3")
    assert f'hx-post="/app/catalog/products/{products[2]}/price"' in listing.text
    modified = admin.get(f"/catalog/products/{products[2]}").json()["dateModified"]
    cell = admin.post(f"/app/catalog/products/{products[2]}/price", data={"price": "777", "date_modified": modified},
                      headers=HTMX)  # fmt: skip
    assert "збережено" in cell.text and 'value="777"' in cell.text
    old = {"price": "1", "date_modified": "2020-01-01T00:00:00"}
    stale = admin.post(f"/app/catalog/products/{products[2]}/price", data=old, headers=HTMX)
    assert 'class="error"' in stale.text

    assert "Масові ціни й імпорт" in admin.get("/app/catalog/prices").text
    assert api(MANAGER).get("/app/catalog/prices").status_code == 403
    form = {"target": "price", "operation": "subtract", "amount": "7", "unit": "amount",
            "category_ids": [str(OTHER_CATEGORY_ID)]}  # fmt: skip
    preview = admin.post("/app/catalog/prices/preview", data=form, headers=HTMX)
    assert "770" in preview.text
    assert "Змінено товарів: 1." in admin.post("/app/catalog/prices/apply", data=form, headers=HTMX).text
    assert (
        "Оберіть категорії"
        in admin.post("/app/catalog/prices/preview", data=form | {"category_ids": []}, headers=HTMX).text
    )

    csv = "product_id;price\n" + f"{products[2]};800\n"
    files = {"file": ("p.csv", csv.encode(), "text/csv")}
    assert "770.0000 → 800" in admin.post("/app/catalog/import/preview", files=files, headers=HTMX).text
    assert "Оновлено товарів: 1" in admin.post("/app/catalog/import/apply", files=files, headers=HTMX).text
    assert "Оберіть CSV-файл" in admin.post("/app/catalog/import/preview", data={"x": "1"}, headers=HTMX).text
