"""The TikTok catalog sync (77) against a fake TikTok, and the Klaviyo module's log. No real TikTok account was used."""

import asyncio
import json
import logging
from decimal import Decimal

import httpx
import pytest

from newerp.application.marketing.integrations import FeedProduct, TikTokSyncService, mask_error
from newerp.config import TikTokSyncSettings, get_settings
from newerp.infrastructure.marketing.integrations import HttpTikTokCatalog
from tests.seed import ADMIN, MANAGER

FAKE_TOKEN = "fake-tiktok-token"


def product(product_id: int, **changes) -> FeedProduct:
    fields = {"product_id": product_id, "name": "Сукня", "description": "<p>Льон <b>і</b> шовк</p>",
              "price": Decimal("1200"), "quantity": 3, "image": "catalog/a.jpg", "seo_keyword": ""}  # fmt: skip
    return FeedProduct(**(fields | changes))


class Store:
    def __init__(self, products: list[FeedProduct]) -> None:
        self.items = products
        self.include_disabled = None

    async def products(self, include_disabled: bool = False) -> list[FeedProduct]:
        self.include_disabled = include_disabled
        return self.items


class Catalog:
    enabled = True

    def __init__(self, refuse: int | None = None) -> None:
        self.batches: list[list[dict]] = []
        self.refuse = refuse

    async def upload(self, products: list[dict]) -> bool:
        self.batches.append(products)
        return len(self.batches) != self.refuse


def service(store: Store, catalog: Catalog, url: str = "https://shop.example/") -> TikTokSyncService:
    return TikTokSyncService(store, catalog, url, "Vidro")


def test_the_products_go_in_tiktoks_format():
    store, catalog = (
        Store([product(1, seo_keyword="suknia"), product(2, quantity=0), product(3, enabled=False)]),
        Catalog(),
    )
    result = asyncio.run(service(store, catalog).sync(user_id=6))
    assert (result.configured, result.uploaded, result.failed) == (True, 3, 0)
    assert store.include_disabled is True  # a product turned off must leave the sale there too
    first, no_stock, disabled = catalog.batches[0]
    assert first == {
        "sku_id": "1", "title": "Сукня", "description": "Льон і шовк", "availability": "IN_STOCK",
        "image_url": "https://shop.example/image/catalog/a.jpg", "brand": "Vidro",
        "product_detail": {"condition": "NEW"}, "price_info": {"price": "1200.00", "currency": "UAH"},
        "landing_page": {"landing_page_url": "https://shop.example/suknia"},
    }  # fmt: skip
    assert no_stock["availability"] == "OUT_OF_STOCK"
    assert no_stock["landing_page"]["landing_page_url"].endswith("route=product/product&product_id=2")
    assert disabled["availability"] == "OUT_OF_STOCK"


def test_a_large_catalog_goes_in_batches_and_a_refused_one_is_counted():
    store, catalog = Store([product(i) for i in range(1, 2502)]), Catalog(refuse=2)
    result = asyncio.run(service(store, catalog).sync(user_id=None))
    assert [len(b) for b in catalog.batches] == [1000, 1000, 501]
    assert (result.uploaded, result.failed) == (1501, 1000)


def test_nothing_goes_out_without_settings_or_a_shop_address():
    off = Catalog()
    off.enabled = False
    result = asyncio.run(service(Store([product(1)]), off).sync(user_id=6))
    assert result.configured is False and off.batches == []
    with pytest.raises(Exception, match="NEWERP_CUSTOMERS__STORE_URL"):
        asyncio.run(service(Store([product(1)]), Catalog(), url=" ").sync(user_id=6))


def client(handler) -> HttpTikTokCatalog:
    settings = TikTokSyncSettings(access_token=FAKE_TOKEN, bc_id="77", catalog_id="88")
    return HttpTikTokCatalog(settings, transport=httpx.MockTransport(handler))


def test_the_client_sends_what_the_old_module_sent():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"code": 0, "message": "OK", "data": {}})

    assert asyncio.run(client(handler).upload([{"sku_id": "1"}])) is True
    request = seen[0]
    assert str(request.url) == "https://business-api.tiktok.com/open_api/v1.3/catalog/product/upload/"
    assert request.headers["Access-Token"] == FAKE_TOKEN
    assert json.loads(request.content) == {"bc_id": "77", "catalog_id": "88", "products": [{"sku_id": "1"}]}


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json={"code": 40001, "message": "Access token is expired"}),
        httpx.Response(500, text="oops"),
        httpx.Response(200, text="not json"),
    ],
)
def test_a_refusal_is_a_failed_batch_and_never_logs_the_token(answer, caplog):
    assert asyncio.run(client(lambda _: answer).upload([{"sku_id": "1"}])) is False
    assert FAKE_TOKEN not in caplog.text


def test_a_dead_network_is_a_failed_batch(caplog):
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"token {FAKE_TOKEN} in a message")

    with caplog.at_level(logging.WARNING):
        assert asyncio.run(client(handler).upload([{"sku_id": "1"}])) is False
    assert FAKE_TOKEN not in caplog.text and "ConnectError" in caplog.text


def test_mask_error_names_the_kind_only():
    assert mask_error(ValueError("secret text")) == "ValueError"


def test_the_endpoint_is_off_without_settings_and_admin_only(api):
    response = api(ADMIN).post("/marketing/tiktok/sync")
    assert response.status_code == 200 and response.json()["configured"] is False
    assert api(MANAGER).post("/marketing/tiktok/sync").status_code == 403


def test_the_settings_never_show_the_token():
    settings = TikTokSyncSettings(access_token=FAKE_TOKEN, bc_id="1", catalog_id="2")
    assert FAKE_TOKEN not in repr(settings) and settings.enabled
    assert not TikTokSyncSettings(access_token=FAKE_TOKEN).enabled


def test_the_page_offers_the_sync_only_when_set_up(api, monkeypatch):
    admin = api(ADMIN)
    assert "Надіслати каталог" not in admin.get("/app/marketing/integrations").text
    config = get_settings().tiktok_sync
    monkeypatch.setattr(config, "access_token", TikTokSyncSettings(access_token=FAKE_TOKEN).access_token)
    monkeypatch.setattr(config, "bc_id", "1")
    monkeypatch.setattr(config, "catalog_id", "2")
    page = admin.get("/app/marketing/integrations").text
    assert "Надіслати каталог" in page and FAKE_TOKEN not in page


def test_klaviyo_log_is_masked_and_admin_only(api, tmp_path, monkeypatch):
    admin = api(ADMIN)
    assert admin.get("/marketing/klaviyo/log").json()["enabled"] is False
    assert "не підключено" in admin.get("/app/marketing/integrations/klaviyo-log").text

    log = tmp_path / "klaviyo_logs.txt"
    log.write_text("event sent\napi_key=fake-key-123\n<script>x</script>\n", encoding="utf-8")
    monkeypatch.setattr(get_settings().admin_tools, "klaviyo_log", str(log))
    view = admin.get("/marketing/klaviyo/log").json()
    assert "event sent" in view["text"] and "fake-key-123" not in view["text"]
    page = admin.get("/app/marketing/integrations/klaviyo-log").text
    assert "event sent" in page and "<script>x" not in page
    assert api(MANAGER).get("/marketing/klaviyo/log").status_code == 403
