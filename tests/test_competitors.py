"""Competitor prices: rules, the page loader without a network, and the API with a fake page."""

import asyncio
import ipaddress
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from newerp.api.competitors import get_page_fetcher
from newerp.application.competitors import PageFetchError
from newerp.domain.common import DomainError
from newerp.domain.competitors import (
    AUTO,
    MANUAL,
    Competitor,
    CompetitorLink,
    extract_price,
    is_public,
    parse_price,
    require_regex,
)
from newerp.infrastructure.competitors import HttpPageFetcher
from newerp.main import app
from tests.seed import CATEGORY_PRODUCT_ID, MANAGER, STOREKEEPER

NOW = datetime(2026, 10, 24, 7, 0)

# ═══ Rules ═══


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 299,00", "1299"), ("1,299.00", "1299"), ("1299.5", "1299.5"), ("1 299 грн", "1299"), ("1.299", "1299"),
        ("12,50", "12.5"), ("1 250", "1250"), ("1 499 ₴", "1499"),
    ],
)  # fmt: skip
def test_parse_reads_shop_price_formats(text, expected):
    assert parse_price(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["", "ціна за запитом", "0,00"])
def test_parse_no_price(text):
    assert parse_price(text) is None


@pytest.mark.parametrize(
    "html",
    [
        '<meta property="product:price:amount" content="1499.00">',
        '<meta content="1499" property="product:price:amount" />',
        '<span itemprop="price" content="1499">1 499 грн</span>',
        '<script type="application/ld+json">{"@type":"Offer","price": "1499.00"}</script>',
    ],
)
def test_extract_standard_markup(html):
    assert extract_price(html, None) == 1499


def test_extract_custom_regex_first_then_standard():
    html = '<div class="price">1 199 грн</div><meta property="product:price:amount" content="1499">'

    assert extract_price(html, 'class="price">([0-9 ]+)') == 1199
    assert extract_price(html, 'class="nothing">([0-9]+)') == 1499
    assert extract_price("<p>Немає в наявності</p>", None) is None


def test_require_regex_needs_a_group_and_valid_syntax():
    assert require_regex("  ") == ""
    with pytest.raises(DomainError, match="групу"):
        require_regex("price: [0-9]+")
    with pytest.raises(DomainError):
        require_regex("(unclosed")


def test_link_must_be_on_the_competitor_site_subdomain_allowed():
    competitor = Competitor("Магазин", "https://www.Shop.ua/", "", True, NOW)

    assert (competitor.host, competitor.site_url) == ("shop.ua", "https://www.shop.ua/")
    assert CompetitorLink(competitor, 101, "https://m.shop.ua/dress-1", NOW).url == "https://m.shop.ua/dress-1"
    for bad in ("https://evilshop.ua/dress-1", "ftp://shop.ua/dress-1", "shop.ua/dress-1"):
        with pytest.raises(DomainError):
            CompetitorLink(competitor, 101, bad, NOW)
    with pytest.raises(DomainError):
        Competitor(" ", "https://shop.ua", "", True, NOW)


def test_record_price_writes_history_only_when_changed_failure_keeps_the_last_price():
    link = CompetitorLink(Competitor("Магазин", "https://shop.ua", "", True, NOW), 101, "https://shop.ua/a", NOW)

    assert link.record_price(Decimal(1000), AUTO, NOW) is not None
    assert link.record_price(Decimal(1000), AUTO, NOW + timedelta(days=1)) is None
    link.record_failure("Сайт відповів HTTP 503.", NOW + timedelta(days=2))
    assert (link.last_price, link.last_error, link.last_checked_at) == (
        1000, "Сайт відповів HTTP 503.", NOW + timedelta(days=2),
    )  # fmt: skip
    assert link.record_price(Decimal(950), MANUAL, NOW + timedelta(days=3)).source == MANUAL
    assert link.last_error == ""
    with pytest.raises(DomainError):
        link.record_price(Decimal("9.999"), MANUAL, NOW)


# ═══ Page loader ═══


class Site:
    """A fake site for httpx.MockTransport: answers by path and records the paths asked."""

    def __init__(self, answers: dict[str, httpx.Response]) -> None:
        self.answers = answers
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        return self.answers.get(request.url.path, httpx.Response(404))


def fetch(site: Site, page: str, host: str = "shop.ua") -> str:
    async def run() -> str:
        async with HttpPageFetcher(httpx.MockTransport(site)) as fetcher:
            return await fetcher.fetch(page, host)

    return asyncio.run(run())


def test_follows_a_redirect_within_the_site_and_reads_the_charset_from_the_header():
    site = Site({
        "/old": httpx.Response(301, headers={"Location": "https://m.shop.ua/new"}),
        "/new": httpx.Response(
            200, content="Ціна 1 299 грн".encode("windows-1251"),
            headers={"Content-Type": "text/html; charset=windows-1251"},
        ),
    })  # fmt: skip

    assert fetch(site, "https://shop.ua/old") == "Ціна 1 299 грн"
    assert site.paths == ["/old", "/new"]


def test_redirect_to_another_site_too_many_redirects_and_errors_are_refused():
    away = Site({"/a": httpx.Response(302, headers={"Location": "https://evil.ua/a"})})
    with pytest.raises(PageFetchError, match="інший сайт"):
        fetch(away, "https://shop.ua/a")

    loop = Site({"/a": httpx.Response(302, headers={"Location": "/a"})})
    with pytest.raises(PageFetchError, match="Забагато переадресацій"):
        fetch(loop, "https://shop.ua/a")

    down = Site({"/a": httpx.Response(503)})
    with pytest.raises(PageFetchError, match="HTTP 503"):
        fetch(down, "https://shop.ua/a")


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("127.0.0.1", False), ("10.1.2.3", False), ("172.20.0.1", False), ("192.168.1.10", False),
        ("169.254.169.254", False), ("100.64.0.1", False), ("::1", False), ("fd00::1", False),
        ("::ffff:192.168.0.1", False), ("8.8.8.8", True), ("2001:4860:4860::8888", True),
    ],
)  # fmt: skip
def test_only_internet_addresses_are_public(address, expected):
    assert is_public(ipaddress.ip_address(address)) is expected


def test_the_real_transport_refuses_localhost():
    async def run() -> None:
        async with HttpPageFetcher() as fetcher:
            await fetcher.fetch("http://localhost:9/", "localhost")

    with pytest.raises(PageFetchError, match="локальну мережу"):
        asyncio.run(run())


# ═══ API ═══


class FakePages:
    def __init__(self) -> None:
        self.html = ""

    async def fetch(self, page: str, site_host: str) -> str:
        return self.html


@pytest.fixture
def pages():
    fake = FakePages()
    app.dependency_overrides[get_page_fetcher] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_page_fetcher, None)


def test_competitor_link_check_manual_price_report_and_history(api, pages):
    client = api(MANAGER)

    created = client.post("/competitors", json={"name": "Конкурент А", "siteUrl": "https://www.shop-a.ua"})
    assert created.status_code == 201
    competitor = created.json()
    link_body = {"competitorId": competitor["id"], "productId": CATEGORY_PRODUCT_ID}
    assert client.post("/competitors/links", json=link_body | {"url": "https://other.ua/x"}).status_code == 400

    added = client.post("/competitors/links", json=link_body | {"url": "https://shop-a.ua/dress"})
    assert added.status_code == 201
    link = added.json()
    assert link["productName"] == "Сорочка лляна оверсайз"
    assert client.post("/competitors/links", json=link_body | {"url": "https://shop-a.ua/dress-2"}).status_code == 400

    pages.html = '<meta property="product:price:amount" content="800.00">'
    assert client.post("/competitors/check").json() == {"checked": 1, "changed": 1, "failed": 0}
    [checked] = client.get(f"/competitors/links?productId={CATEGORY_PRODUCT_ID}").json()
    assert (checked["lastPrice"], checked["lastError"]) == (800, "")

    pages.html = "<p>Немає в наявності</p>"
    failed = client.post(f"/competitors/links/{link['id']}/check").json()
    assert (failed["lastPrice"], failed["lastError"]) == (800, "Ціну на сторінці не знайдено.")

    assert client.post(f"/competitors/links/{link['id']}/price", json={"price": 750}).status_code == 200
    history = client.get(f"/competitors/links/{link['id']}/history").json()
    assert [(h["price"], h["source"]) for h in history] == [(750, "manual"), (800, "auto")]

    report = client.get("/competitors/report").json()
    column = [c["id"] for c in report["competitors"]].index(competitor["id"])
    [row] = report["rows"]
    assert (row["prices"][column], row["ourPrice"], row["differencePercent"]) == (750, 900, 20)

    assert client.get("/competitors").json()[0]["links"] == 1
    assert client.delete(f"/competitors/links/{link['id']}").status_code == 204
    assert client.get(f"/competitors/links/{link['id']}/history").status_code == 404


def test_storekeeper_has_no_access(api):
    assert api(STOREKEEPER).get("/competitors").status_code == 403
