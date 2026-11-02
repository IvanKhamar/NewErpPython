"""Competitor price and report pages. The competitors' sites are a fake: no request leaves the test."""

import pytest

from newerp.api.competitors import get_page_fetcher
from newerp.main import app
from tests.seed import CATEGORY_PRODUCT_ID, COMPLETED, MANAGER, STOREKEEPER
from tests.test_competitors import FakePages

HTMX = {"HX-Request": "true"}


@pytest.fixture
def pages():
    fake = FakePages()
    app.dependency_overrides[get_page_fetcher] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_page_fetcher, None)


def test_competitor_page_check_manual_price_and_history(api, pages):
    client = api(MANAGER)
    form = {"competitor_id": "", "name": "Конкурент веб", "site_url": "https://www.shop-web.ua", "price_regex": "",
            "is_active": "1"}  # fmt: skip
    assert client.post("/app/competitors/save", data=form | {"site_url": ""}).status_code == 400
    assert client.post("/app/competitors/save", data=form, follow_redirects=False).status_code == 303
    competitor_id = next(c["id"] for c in client.get("/competitors").json() if c["name"] == "Конкурент веб")
    assert "shop-web.ua" in client.get(f"/app/competitors/{competitor_id}/edit").text

    link = {"competitor_id": str(competitor_id), "product_id": str(CATEGORY_PRODUCT_ID)}
    other_site = client.post("/app/competitors/links", data=link | {"url": "https://other.ua/x"}, headers=HTMX)
    assert 'role="alert"' in other_site.text
    added = client.post("/app/competitors/links", data=link | {"url": "https://shop-web.ua/shirt"}, headers=HTMX)
    assert "Сторінку додано." in added.text
    link_id = client.get(f"/competitors/links?productId={CATEGORY_PRODUCT_ID}").json()[0]["id"]

    pages.html = '<meta property="product:price:amount" content="800.00">'
    assert "ціна змінилась: 1" in client.post("/app/competitors/check", headers=HTMX).text
    pages.html = "<p>Немає</p>"
    assert "Ціну на сторінці не знайдено." in client.post(f"/app/competitors/links/{link_id}/check").text
    assert "Ціну записано." in client.post(f"/app/competitors/links/{link_id}/price", data={"price": "750"}).text
    history = client.get(f"/app/competitors/links/{link_id}/history", headers=HTMX).text
    assert "вручну" in history
    assert "автоматично" in history
    assert "Сторінку видалено." in client.post(f"/app/competitors/links/{link_id}/delete").text
    assert api(STOREKEEPER).get("/app/competitors").status_code == 403


def test_reports_show_sales_products_and_statuses(api):
    client = api(MANAGER)
    sales = client.get("/app/reports?view=sales&since=2026-01-01&group=2").text
    assert "Разом" in sales
    assert '<option value="2" selected>' in sales
    products = client.get(f"/app/reports?view=products&since=2026-01-01&status={COMPLETED}").text
    assert "Продано" in products
    statuses = client.get("/app/reports?view=statuses&since=2026-01-01&until=2026-12-31").text
    assert "Статус повернення" in statuses
    assert client.get("/app/reports?view=unknown&since=вчора").status_code == 200  # falls back to the sales
