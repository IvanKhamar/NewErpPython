"""Marketing and partner pages: birthdays, the contact export, gift vouchers (also in the order card), partners."""

from datetime import datetime

from tests.seed import CATEGORY_ID, CATEGORY_PRODUCT_ID, MANAGER, STOREKEEPER
from tests.test_new_orders import request as order_request
from tests.test_partners import add_catalog_data

HTMX = {"HX-Request": "true"}
PHONE = "380670000002"  # the phone of order 2 in the shared test data


def test_birthday_greeting_with_a_coupon(api, db):
    today = datetime.now()
    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE dd_order SET birthday_day = %s, birthday_month = %s WHERE order_id = 2", (today.day, today.month)
        )
    client = api(MANAGER)
    page = client.get("/app/marketing/birthdays?period=0").text
    assert f'value="{PHONE}"' in page

    panel = client.get(f"/app/marketing/birthdays/greet?phone={PHONE}&name=Тарас", headers=HTMX).text
    assert 'href="/app/orders/2"' in panel
    coupon = client.post("/app/marketing/birthdays/coupon-code", headers=HTMX).text
    code = coupon.split('value="')[1].split('"')[0]
    assert code.startswith("DN")

    sent = client.post("/app/marketing/birthdays/sms", data={"phone": PHONE, "text": "Вітаємо!", "coupon_code": code})
    assert "SMS не надіслано" in sent.text  # in tests the SMS goes to the stub, the coupon is made anyway
    again = client.post("/app/marketing/birthdays/sms", data={"phone": PHONE, "text": "Ще", "coupon_code": code})
    assert 'role="alert"' in again.text
    bulk = client.post("/app/marketing/birthdays/sms-bulk", data={"phones": [PHONE], "text": "Привіт"}, headers=HTMX)
    assert "Надіслано 0, не надіслано 1" in bulk.text
    assert api(STOREKEEPER).get("/app/marketing/birthdays").status_code == 403


def test_contact_export_is_a_csv_download(api):
    client = api(MANAGER)
    csv = client.get("/app/marketing/export/contacts.csv?field=Phone&sources=Orders&since=2020-01-01")
    assert csv.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv.headers["content-disposition"]
    assert client.get("/app/marketing/export/contacts.csv?field=Phone&since=вчора").status_code == 400


def test_voucher_is_made_and_pays_part_of_an_order(api):
    client = api(MANAGER)
    body = order_request("+380501112299", quantity=1) | {"firstname": "Софія", "lastname": "Веб"}
    order_id = client.post("/orders", json=body).json()["orderId"]

    form = {"voucher_id": "", "code": "", "amount": "багато", "from_name": "", "from_email": "", "to_name": "Софія",
            "to_email": "", "message": "", "active": "1"}  # fmt: skip
    wrong = client.post("/app/marketing/vouchers/save", data=form)
    assert wrong.status_code == 400
    assert "Сума: потрібне число" in wrong.text
    created = client.post("/app/marketing/vouchers/save", data=form | {"amount": "500"}, follow_redirects=False)
    voucher_url = created.headers["location"]
    card = client.get(voucher_url).text
    code = client.get("/marketing/vouchers?search=Софія").json()[0]["code"]
    assert code in card

    section = f"/app/marketing/orders/{order_id}/voucher"
    assert "Оплатити сертифікатом" in client.get(section, headers=HTMX).text
    spent = client.post(f"{section}/redeem", data={"code": code.lower(), "amount": ""}, headers=HTMX)
    assert "Сертифікат списано" in spent.text
    assert 'role="alert"' in client.post(f"{section}/redeem", data={"code": code}, headers=HTMX).text
    assert "гроші повернулися" in client.post(f"{section}/release", headers=HTMX).text

    listed = client.get("/app/marketing/vouchers?search=Софія", headers=HTMX).text
    assert code in listed
    deleted = client.post(f"{voucher_url}/delete", headers=HTMX)
    assert deleted.headers["HX-Redirect"] == "/app/marketing/vouchers"


def test_partner_is_saved_with_its_maps_and_deleted(api, db):
    add_catalog_data(db)
    client = api(MANAGER)
    assert "Новий партнер" in client.get("/app/partners/new").text
    row = client.get(f"/app/partners/category-row?add={CATEGORY_ID}", headers=HTMX).text
    assert f'name="category_id" value="{CATEGORY_ID}"' in row
    found = client.get("/app/partners/products?product_search=лляна", headers=HTMX).text
    assert f"product_id={CATEGORY_PRODUCT_ID}" in found

    form = {"partner_id": "", "name": "EVA веб", "code": "eva-web", "vendor_name": "Vidro", "default_quantity": "3",
            "feed_url": "", "feed_enabled": "1", "is_active": "1", "notes": "",
            "category_id": [str(CATEGORY_ID)], "category_size": [""], "category_external_id": ["77"],
            "category_external_name": ["Сорочки"], "category_supply": ["10"], "category_sale": ["20"],
            "product_id": [str(CATEGORY_PRODUCT_ID)], "product_size": ["M"], "product_external_id": ["p1"],
            "product_external_name": [""], "product_supply": [""], "product_sale": ["1100"]}  # fmt: skip
    wrong = client.post("/app/partners/save", data=form | {"default_quantity": "2,5"}, headers=HTMX)
    assert "ціле число" in wrong.text
    saved = client.post("/app/partners/save", data=form, headers=HTMX)
    url = saved.headers["HX-Redirect"]
    partner_id = int(url.rsplit("/", 1)[1])

    partner = client.get(f"/partners/{partner_id}").json()
    assert [r["externalId"] for r in partner["categoryMap"]] == ["77"]
    assert [(r["sizeName"], r["priceSale"]) for r in partner["productMap"]] == [("M", 1100)]
    assert 'value="77"' in client.get(url).text
    assert 'href="/feeds/eva-web.xml"' in client.get("/app/partners").text
    assert client.post(f"/app/partners/{partner_id}/delete", headers=HTMX).headers["HX-Redirect"] == "/app/partners"
    assert client.get(f"/partners/{partner_id}").status_code == 404
