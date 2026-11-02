"""The editors of a production model: cut parts and BOMs, product links, the model stocktaking."""

from tests.seed import ADMIN, PRODUCT_ID, STOREKEEPER
from tests.test_production import stock_item
from tests.test_web_production import new_model, operation

HTMX = {"HX-Request": "true"}


def sizes(client, model_id: int) -> dict[str, int]:
    return {size["name"]: size["id"] for size in client.get(f"/production/models/{model_id}").json()["sizes"]}


def test_parts_and_both_boms_are_edited_on_one_page(api):
    client = api(STOREKEEPER)
    linen = stock_item(client, "Льон для специфікації", "м")
    thread = stock_item(client, "Нитки для специфікації", "шт")
    model_id = new_model(client, "Сукня специфікацій", ("S", "50"), ("M", "50"))
    size_m = sizes(client, model_id)["M"]
    url = f"/app/production/models/{model_id}/specs"
    assert "Деталі крою" in client.get(url).text

    assert "Деталь додано." in client.post(f"{url}/add-component", data={"name": "Перед"}, headers=HTMX).text
    front = client.get(f"/production/models/{model_id}").json()["components"][0]["id"]
    bom = {"component_id": str(front), "size_id": ["", str(size_m), ""], "item_id": [str(linen), str(linen), ""],
           "qty": ["1,2", "1,4", ""]}  # fmt: skip
    assert "Специфікацію крою збережено." in client.post(f"{url}/component-bom", data=bom, headers=HTMX).text
    sewn = {"size_id": ["", "", ""], "what": [f"c:{front}", f"i:{thread}", ""], "qty": ["1", "0,5", ""]}
    assert "Специфікацію пошиву збережено." in client.post(f"{url}/sewn-bom", data=sewn, headers=HTMX).text
    wrong = client.post(f"{url}/sewn-bom", data={"size_id": [""], "what": [f"i:{thread}"], "qty": ["багато"]})
    assert "Кількість: потрібне число" in wrong.text

    model = client.get(f"/production/models/{model_id}").json()
    [component] = model["components"]
    assert sorted((b["sizeId"] or 0, b["qtyPerUnit"]) for b in component["bom"]) == [(0, 1.2), (size_m, 1.4)]
    assert {(b["componentId"], b["itemId"]) for b in model["sewnBom"]} == {(front, None), (None, thread)}
    page = client.get(url).text
    assert f'value="c:{front}" selected' in page  # the saved rows come back selected

    rename = {"component_id": str(front), "name": "Перед сукні", "sort_order": "1"}
    assert "Деталь змінено." in client.post(f"{url}/rename-component", data=rename, headers=HTMX).text
    operation(client, model_id, "cut", {size_m: "2"})  # the part now has a cut stock
    refused = client.post(f"{url}/delete-component", data={"component_id": str(front)}, headers=HTMX)
    assert "не можна видалити" in refused.text


def test_links_and_size_options(api):
    client = api(ADMIN)
    model_id = new_model(client, "Сорочка прив'язок", ("M", "100"))
    size_m = sizes(client, model_id)["M"]
    url = f"/app/production/models/{model_id}/links"
    form = {"product_ids": f"{PRODUCT_ID}", "category_id": ["5", ""], "name_filter": ["сорочка", ""],
            "match_exact": ["1", "0"], "size_id": [str(size_m)], "option_value_id": ["46"]}  # fmt: skip
    assert "не номер товару" in client.post(url, data=form | {"product_ids": "101, сто"}, headers=HTMX).text
    assert "язки збережено" in client.post(url, data=form, headers=HTMX).text  # the apostrophe comes escaped

    model = client.get(f"/production/models/{model_id}").json()
    assert model["productIds"] == [PRODUCT_ID]
    assert [(c["categoryId"], c["nameFilter"], c["matchExact"]) for c in model["categories"]] == [(5, "сорочка", True)]
    assert model["sizes"][0]["optionValueId"] == 46
    assert 'value="сорочка"' in client.get(url).text


def test_model_stocktaking_counts_cut_sewn_and_parts(api):
    client = api(STOREKEEPER)
    model_id = new_model(client, "Сукня інвентаризації веб", ("M", "100"))
    size_m = sizes(client, model_id)["M"]
    client.post(f"/app/production/models/{model_id}/specs/add-component", data={"name": "Спинка"})
    back = client.get(f"/production/models/{model_id}").json()["components"][0]["id"]
    url = f"/app/production/models/{model_id}/stocktaking"
    assert "Спинка" in client.get(url).text

    form = {"warehouse_id": "", "size_id": [str(size_m)], "cut": ["3"], "sewn": ["2"],
            "part_component_id": [str(back)], "part_size_id": [str(size_m)], "part_actual": ["5"],
            "comment": "веб"}  # fmt: skip
    counted = client.post(url, data=form, headers=HTMX).text
    assert "Крій M: 0 → 3" in counted
    assert "Пошите M: 0 → 2" in counted
    assert "Змінено запасів деталей: 1" in counted
    model = client.get(f"/production/models/{model_id}").json()
    assert (model["sizes"][0]["cutStock"], model["sizes"][0]["sewnStock"]) == (3, 2)
    assert model["components"][0]["stock"][str(size_m)] == 5

    nothing = client.post(url, data={"warehouse_id": "", "size_id": [str(size_m)], "cut": [""], "sewn": [""]})
    assert "Немає жодної порахованої" in nothing.text
