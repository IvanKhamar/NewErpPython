"""Production pages at /app/production: models, the card with the operations, the reports."""

from tests.helpers import create
from tests.seed import ADMIN, MANAGER, STOREKEEPER

HTMX = {"HX-Request": "true"}


def model_form(name: str, *sizes: tuple[str, str], **changes: str) -> dict:
    form = {"name": name, "type": "clothing", "min_total": "20", "sort_order": "0"} | changes
    return form | {"size_name": [size for size, _ in sizes] + [""], "size_percent": [p for _, p in sizes] + [""]}


def new_model(client, name: str, *sizes: tuple[str, str], **changes: str) -> int:
    response = client.post(
        "/app/production/models/new", data=model_form(name, *sizes, **changes), follow_redirects=False
    )
    assert response.status_code == 303, response.text
    return int(response.headers["location"].rsplit("/", 1)[1])


def stock(client, model_id: int) -> dict[str, tuple[float, float]]:
    model = client.get(f"/production/models/{model_id}").json()
    return {size["name"]: (size["cutStock"], size["sewnStock"]) for size in model["sizes"]}


def operation(client, model_id: int, code: str, sizes: dict[int, str], **extra: str):
    form = {"operation": code, "size_id": [str(s) for s in sizes], "quantity": list(sizes.values())} | extra
    return client.post(f"/app/production/models/{model_id}/operations", data=form, headers=HTMX)


def test_menu_and_access(api):
    page = api(STOREKEEPER).get("/app/production")
    assert 'href="/app/production/plan"' in page.text
    assert api(MANAGER).get("/app/production/log").status_code == 403


def test_model_is_created_edited_and_filtered(api):
    client = api(STOREKEEPER)
    wrong = client.post("/app/production/models/new", data=model_form("Сукня веб", ("S", "50"), min_total="1,5"))
    assert wrong.status_code == 400
    assert "потрібне ціле число" in wrong.text
    assert 'value="Сукня веб"' in wrong.text

    model_id = new_model(client, "Сукня веб", ("S", "40"), ("M", "60"))
    assert list(stock(client, model_id)) == ["S", "M"]  # the empty size row is skipped

    edit = client.get(f"/app/production/models/{model_id}/edit")
    assert 'value="60"' in edit.text
    form = model_form("Сукня веб літня", ("S", "40"), ("M", "60"), ("L", "0"))
    saved = client.post(f"/app/production/models/{model_id}/edit", data=form, follow_redirects=False)
    assert saved.status_code == 303
    assert list(stock(client, model_id)) == ["S", "M", "L"]

    clothing = client.get("/app/production?type=clothing", headers=HTMX).text
    assert "Сукня веб літня" in clothing
    assert "Сукня веб літня" not in client.get("/app/production?type=ready_made", headers=HTMX).text


def test_operations_change_stock_and_refuse_too_much(api):
    client = api(STOREKEEPER)
    model_id = new_model(client, "Сорочка веб", ("S", "50"), ("M", "50"))
    sizes = {size["name"]: size["id"] for size in client.get(f"/production/models/{model_id}").json()["sizes"]}
    s, m = sizes["S"], sizes["M"]

    cut = operation(client, model_id, "cut", {s: "10", m: ""}, comment="веб")
    assert "Записано в журнал." in cut.text
    assert "S: 0 → 10" in cut.text
    operation(client, model_id, "to-sewing", {s: "6", m: ""})
    sewn = operation(client, model_id, "sewn", {s: "6", m: "0"})
    assert "Записано в журнал." in sewn.text
    assert stock(client, model_id) == {"S": (4, 6), "M": (0, 0)}

    too_much = operation(client, model_id, "ship", {s: "7", m: ""})
    assert 'role="alert"' in too_much.text
    assert stock(client, model_id)["S"] == (4, 6)
    assert operation(client, model_id, "unknown", {s: "1"}).status_code == 404

    log = client.get("/app/production/log?stage=sewn").text
    assert "Сорочка веб" in log
    assert "Пошито / прихід" in log
    assert "Сорочка веб" in client.get("/app/production/by-warehouse").text
    assert client.get("/app/production/plan?sizes=all").status_code == 200


def test_sewn_pieces_move_between_warehouses_and_a_model_is_hidden(api):
    client = api(ADMIN)
    warehouse = create(client, "/locations", {"name": "Шоурум веб-тесту"})
    model_id = new_model(client, "Жакет веб", ("M", "100"), type="ready_made")
    size_id = client.get(f"/production/models/{model_id}").json()["sizes"][0]["id"]
    card = client.get(f"/app/production/models/{model_id}").text
    assert "Прихід" in card
    assert 'value="cut"' not in card  # ready goods are not cut

    operation(client, model_id, "sewn", {size_id: "5"})
    form = {"size_id": str(size_id), "from_warehouse_id": "", "to_warehouse_id": str(warehouse), "quantity": "2"}
    moved = client.post(f"/app/production/models/{model_id}/transfers", data=form | {"comment": ""})
    assert "Переміщено." in moved.text
    [places] = client.get(f"/production/models/{model_id}/places").json()
    assert [(p["warehouseId"], p["quantity"]) for p in places["places"]] == [(None, 3), (warehouse, 2)]

    hidden = client.post(f"/app/production/models/{model_id}/deactivate", headers=HTMX)
    assert hidden.headers["HX-Redirect"] == "/app/production"
    assert "Жакет веб" not in client.get("/app/production").text
    assert client.post("/app/production/models/999999/deactivate").status_code == 404
