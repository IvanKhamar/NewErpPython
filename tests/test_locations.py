from tests.helpers import create, open_book, save_book, upload
from tests.seed import MANAGER, STOREKEEPER


def test_locations_crud_and_excel_round_trip(api):
    manager = api(MANAGER)
    location_id = create(manager, "/locations", {"name": "Склад на Подолі", "city": "Київ", "telephone": "0671234567"})
    other = create(manager, "/locations", {"name": "Тимчасовий"})
    assert manager.delete(f"/locations/{other}").status_code == 204
    assert manager.post("/locations", json={"name": " "}).status_code == 400

    book = open_book(manager.get("/locations/export.xlsx").content)
    sheet = book.worksheets[0]
    row = next(r for r in sheet.iter_rows(min_row=2) if r[0].value == location_id)
    assert (sheet.cell(1, 1).value, row[4].value) == ("ID", "0671234567")
    row[1].value = "Склад на Подолі (новий)"
    new_row = sheet.max_row + 1
    sheet.cell(new_row, 2).value = "Пункт видачі у Львові"
    sheet.cell(new_row, 6).value = "Львів"

    result = upload(manager, "/locations/import", save_book(book)).json()
    assert (result["added"], result["updated"]) == (1, 1)
    names = [location["name"] for location in manager.get("/locations").json()]
    assert names == ["Склад на Подолі (новий)", "Пункт видачі у Львові"]

    assert upload(manager, "/locations/import", "не excel".encode()).status_code == 400
    assert api(STOREKEEPER).get("/locations").status_code == 403
