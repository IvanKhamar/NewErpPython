"""Marketing integrations (items 59–61): Klaviyo exports and switches, the TikTok catalog feed, Instagram posts."""

import asyncio
from datetime import datetime

import httpx
import pytest

from newerp.api import marketing as marketing_api
from newerp.application.marketing.integrations import InstagramMedia
from newerp.config import get_settings
from newerp.domain.common import DomainError
from newerp.infrastructure.marketing.integrations import HttpInstagramClient
from tests.seed import ADMIN, CATEGORY_PRODUCT_ID, LANGUAGE_ID, MANAGER, insert


def customer(db, customer_id: int, email: str, newsletter: bool) -> None:
    insert(
        db, "dd_customer", customer_id=customer_id, customer_group_id=1, language_id=LANGUAGE_ID, firstname="Ірина",
        lastname="=HYPERLINK(1)", email=email, telephone="0671112233", fax="", password="", salt="", custom_field="",
        ip="", status=True, safe=False, token="", code="", address_id=0, newsletter=newsletter,
        date_added="2026-01-01",
    )  # fmt: skip


def test_klaviyo_exports_are_for_the_admin_and_safe_for_excel(api, db):
    customer(db, 60, "sub@example.com", True)
    customer(db, 61, "nosub@example.com", False)
    admin = api(ADMIN)
    assert api(MANAGER).get("/marketing/klaviyo/export/customers.csv").status_code == 403

    customers = admin.get("/marketing/klaviyo/export/customers.csv")
    assert customers.headers["content-type"].startswith("text/csv")
    lines = customers.text.lstrip("﻿").splitlines()
    assert lines[0] == "email,firstname,lastname,telephone"
    assert "sub@example.com,Ірина,'=HYPERLINK(1),0671112233" in lines  # no formula for Excel
    assert admin.get("/marketing/klaviyo/export/subscribers.csv").text.lstrip("﻿").splitlines() == [
        "email",
        "sub@example.com",
    ]
    assert "nosub@example.com" in admin.get("/marketing/klaviyo/export/unsubscribers.csv").text
    assert admin.get("/marketing/klaviyo/export/everyone.csv").status_code == 400


def test_klaviyo_switches_keep_its_api_keys(api, db):
    insert(db, "dd_setting", store_id=0, code="module_klaviyo", key="module_klaviyo_private_api", value="pk_fake",
           serialized=False)  # fmt: skip
    admin = api(ADMIN)
    saved = admin.put(
        "/marketing/klaviyo/settings", json={"enabled": True, "tracking": True, "newsletterListId": "AbC12"}
    )
    assert saved.json() == {"enabled": True, "tracking": True, "newsletterListId": "AbC12"}
    assert "pk_fake" not in saved.text
    assert admin.put("/marketing/klaviyo/settings", json={"newsletterListId": "x; drop"}).status_code == 400
    with db.cursor() as cursor:
        cursor.execute("SELECT value FROM dd_setting WHERE `key` = 'module_klaviyo_private_api'")
        assert cursor.fetchone() == ("pk_fake",)


def test_tiktok_feed_needs_its_token(api, db, monkeypatch):
    client = api()  # a feed reader has no account
    assert client.get("/feeds/tiktok.csv").status_code == 404  # no token in the settings: no feed
    monkeypatch.setattr(get_settings().tiktok_feed, "token", type(get_settings().tiktok_feed.token)("feed-secret"))
    assert client.get("/feeds/tiktok.csv?token=wrong").status_code == 401
    assert client.get("/feeds/tiktok.csv?token=feed-secret").status_code == 400  # no storefront address yet

    monkeypatch.setattr(get_settings().customers, "store_url", "https://shop.example/")
    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE dd_product SET image = 'catalog/shirt.jpg' WHERE product_id = %s", (CATEGORY_PRODUCT_ID,)
        )
        cursor.execute("UPDATE dd_product_description SET description = %s WHERE product_id = %s",
                       ("<p>Льон, <b>100%</b></p>", CATEGORY_PRODUCT_ID))  # fmt: skip
    feed = client.get("/feeds/tiktok.csv", headers={"X-Feed-Token": "feed-secret"})
    lines = feed.text.splitlines()
    assert lines[0] == "sku_id,title,description,availability,condition,price,link,image_link,brand"
    row = next(line for line in lines if line.startswith(f"{CATEGORY_PRODUCT_ID},"))
    assert '"Льон, 100%"' in row and "900.00 UAH" in row and "https://shop.example/image/catalog/shirt.jpg" in row
    assert f"https://shop.example/index.php?route=product/product&product_id={CATEGORY_PRODUCT_ID}" in row


class FakeInstagram:
    refreshed = False

    def __init__(self, *_):
        pass

    async def refresh(self, token):
        FakeInstagram.refreshed = True
        return "fresh-token"

    async def media(self, token):
        assert token == "fresh-token"
        return [
            InstagramMedia(
                "17890000000000001",
                "IMAGE",
                "https://scontent.cdninstagram.com/1.jpg",
                "",
                "Нова колекція",
                "https://www.instagram.com/p/1/",
                datetime(2026, 11, 1, 10),
            ),  # fmt: skip
            InstagramMedia("not-a-number", "IMAGE", "", "", "", "", None),
        ]

    async def download(self, url):
        raise AssertionError("no picture folder is set, so nothing is downloaded")


def test_instagram_refreshes_an_old_token_and_adds_new_posts(api, db, monkeypatch):
    assert api(ADMIN).post("/marketing/instagram/update").json()["configured"] is False  # no token yet
    for key, value in (("module_instagram_token", "old-token"), ("module_instagram_token_date", "2026:01:02 10:11:12")):
        insert(db, "dd_setting", store_id=0, code="module_instagram", key=key, value=value, serialized=False)
    monkeypatch.setattr(marketing_api, "HttpInstagramClient", FakeInstagram)

    result = api(ADMIN).post("/marketing/instagram/update").json()
    assert result == {"configured": True, "tokenRefreshed": True, "added": 1, "picturesSaved": 0}
    with db.cursor() as cursor:
        cursor.execute("SELECT value FROM dd_setting WHERE `key` = 'module_instagram_token'")
        assert cursor.fetchone() == ("fresh-token",)
        cursor.execute("SELECT instagram_id, caption FROM dd_instagram")
        assert cursor.fetchall() == ((17890000000000001, "Нова колекція"),)
    # The token is fresh now and the post is known.
    assert api(ADMIN).post("/marketing/instagram/update").json()["added"] == 0
    assert api(MANAGER).post("/marketing/instagram/update").status_code == 403


@pytest.mark.parametrize(
    "url",
    ["http://scontent.cdninstagram.com/1.jpg", "https://evil.example/1.jpg", "https://cdninstagram.com.evil.example/x"],
)
def test_pictures_come_only_from_instagram_over_https(url):
    client = HttpInstagramClient(httpx.MockTransport(lambda _: httpx.Response(200, content=b"x")))
    with pytest.raises(DomainError):
        asyncio.run(client.download(url))


def test_a_too_big_picture_is_refused():
    big = httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 5_000_001))
    with pytest.raises(DomainError, match="5 МБ"):
        asyncio.run(HttpInstagramClient(big).download("https://scontent.cdninstagram.com/1.jpg"))


def test_web_integrations_page(api):
    admin = api(ADMIN)
    assert "Klaviyo" in admin.get("/app/marketing/integrations").text
    assert api(MANAGER).get("/app/marketing/integrations").status_code == 403
    saved = admin.post("/app/marketing/integrations/klaviyo", data={"enabled": "1", "newsletter_list_id": "Xy9"},
                       headers={"HX-Request": "true"})  # fmt: skip
    assert "Збережено." in saved.text and 'value="Xy9"' in saved.text
