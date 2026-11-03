"""Marketing (items 57, 58): tracking codes with their orders, gift vouchers by e-mail, voucher themes."""

import pytest

from newerp.api import marketing as marketing_api
from newerp.config import get_settings
from tests.seed import ADMIN, CATEGORY_ORDER_ID, LANGUAGE_ID, MANAGER, STOREKEEPER, insert

HTMX = {"HX-Request": "true"}


class FakeMail:
    is_configured = True

    def __init__(self):
        self.sent = []

    async def send(self, to, subject, text):
        self.sent.append((to, subject, text))


@pytest.fixture(scope="module", autouse=True)
def theme(db):
    insert(db, "dd_voucher_theme", voucher_theme_id=1, image="catalog/vouchers/holiday.jpg")
    insert(db, "dd_voucher_theme_description", voucher_theme_id=1, language_id=LANGUAGE_ID, name="Свято")


def test_tracking_codes_count_their_orders(api, db, monkeypatch):
    monkeypatch.setattr(get_settings().customers, "store_url", "https://shop.example/")
    client = api(MANAGER)
    code = client.post("/marketing/tracking", json={"name": "Instagram осінь", "code": "insta-autumn"})
    assert code.status_code == 201, code.text
    code = code.json()
    assert (code["orders"], code["link"]) == (0, "https://shop.example/?tracking=insta-autumn")

    with db.cursor() as cursor:
        cursor.execute("UPDATE dd_order SET marketing_id = %s WHERE order_id = %s", (code["id"], CATEGORY_ORDER_ID))
    [listed] = [c for c in client.get("/marketing/tracking").json() if c["id"] == code["id"]]
    assert (listed["orders"], listed["revenue"]) == (1, 900)

    assert client.post("/marketing/tracking", json={"name": "Дубль", "code": "insta-autumn"}).status_code == 409
    assert client.post("/marketing/tracking", json={"name": "X", "code": "with space"}).status_code == 400
    assert client.post("/marketing/tracking", json={"name": "X", "code": "<script>"}).status_code == 400
    renamed = client.put(f"/marketing/tracking/{code['id']}", json=code | {"name": "Instagram зима"}).json()
    assert renamed["name"] == "Instagram зима"
    assert client.delete(f"/marketing/tracking/{code['id']}").status_code == 204
    assert client.delete(f"/marketing/tracking/{code['id']}").status_code == 404
    assert api(STOREKEEPER).get("/marketing/tracking").status_code == 403


def test_voucher_goes_by_email_only_with_mail_settings(api, monkeypatch):
    client = api(ADMIN)
    voucher = client.post("/marketing/vouchers", json={
        "toName": "Софія", "toEmail": "sofia@example.com", "fromName": "Олена", "amount": 1500, "themeId": 1,
        "message": "З днем народження!",
    }).json()  # fmt: skip

    # The stub: nothing can be sent without SMTP settings.
    assert client.post(f"/marketing/vouchers/{voucher['id']}/email").status_code == 400

    mail = FakeMail()
    monkeypatch.setattr(marketing_api, "get_mail", lambda: mail)
    sent = client.post(f"/marketing/vouchers/{voucher['id']}/email").json()
    assert sent == {"sent": True, "to": "sofia@example.com"}
    [(to, subject, text)] = mail.sent
    assert subject == "Подарунковий сертифікат Vidro" and voucher["code"] in text and "від Олена" in text
    assert "З днем народження!" in text

    other = client.post(f"/marketing/vouchers/{voucher['id']}/email?to=olena@example.com").json()
    assert other["to"] == "olena@example.com"
    bad = client.post(
        f"/marketing/vouchers/{voucher['id']}/email", params={"to": "x@example.com\nBcc: all@example.com"}
    )
    assert bad.status_code == 400  # no header injection
    assert client.post("/marketing/vouchers/9999/email").status_code == 404


def test_voucher_themes(api):
    client = api(MANAGER)
    [holiday] = client.get("/marketing/vouchers/themes/details").json()
    assert (holiday["name"], holiday["image"]) == ("Свято", "catalog/vouchers/holiday.jpg")
    new = client.post("/marketing/vouchers/themes", json={"name": "Зима", "image": "catalog/vouchers/winter.png"})
    assert new.status_code == 201
    new = new.json()
    assert client.post("/marketing/vouchers/themes", json={"name": "X", "image": "../x.png"}).status_code == 400
    assert client.post("/marketing/vouchers/themes", json={"name": "X", "image": ""}).status_code == 400
    assert (
        client.put(f"/marketing/vouchers/themes/{new['id']}", json=new | {"name": "Зимова"}).json()["name"] == "Зимова"
    )
    assert client.delete(f"/marketing/vouchers/themes/{new['id']}").status_code == 204
    assert holiday["vouchers"] == 1  # the voucher of the previous test
    assert client.delete("/marketing/vouchers/themes/1").status_code == 400


def test_web_tracking_themes_and_voucher_email(api, monkeypatch):
    client = api(MANAGER)
    assert client.get("/app/marketing/tracking").status_code == 200
    added = client.post("/app/marketing/tracking/0/save", data={"name": "TikTok", "code": "tt-1"}, headers=HTMX)
    assert "Збережено." in added.text and 'value="tt-1"' in added.text
    assert 'class="error"' in client.post("/app/marketing/tracking/0/save", data={"name": "X", "code": "a b"},
                                          headers=HTMX).text  # fmt: skip
    assert client.get("/app/marketing/vouchers/themes").status_code == 200
    theme = client.post("/app/marketing/vouchers/themes/0/save", data={"name": "Весна", "image": "catalog/v/s.jpg"},
                        headers=HTMX)  # fmt: skip
    assert 'value="Весна"' in theme.text

    mail = FakeMail()
    monkeypatch.setattr(marketing_api, "get_mail", lambda: mail)
    voucher = api(ADMIN).post("/marketing/vouchers", json={"toName": "Ірина", "amount": 300}).json()
    page = client.get(f"/app/marketing/vouchers/{voucher['id']}")
    assert "Надіслати лист" in page.text
    sent = client.post(f"/app/marketing/vouchers/{voucher['id']}/email", data={"to": "iryna@example.com"}, headers=HTMX)
    assert "Лист надіслано на iryna@example.com." in sent.text
