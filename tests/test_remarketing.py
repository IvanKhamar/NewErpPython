"""UTM report and the remarketing data of an order (the storefront module's dd_remarketing_orders). Own database."""

import pytest

from tests.seed import CATEGORY_ORDER_ID, MANAGER, ORDER_ID, STOREKEEPER

HTMX = {"HX-Request": "true"}


@pytest.fixture(scope="module")
def rows(db):
    with db.cursor() as cursor:
        # The module writes 0000-00-00 00:00:00 for an event it did not send; a session without strict dates stores it.
        cursor.execute("SET SESSION sql_mode = ''")
        for order_id, source, medium, campaign, ga4 in (
            (ORDER_ID, "ig", "paid", "autumn", "2026-10-01 12:05:00"),  # 1500
            (2, "google", "cpc", "brand", "0000-00-00 00:00:00"),  # 2000
            (CATEGORY_ORDER_ID, "ig", "paid", "summer", "0000-00-00 00:00:00"),  # 900, in September
        ):
            cursor.execute(
                "INSERT INTO dd_remarketing_orders (order_id, first_referrer, utm_source, utm_medium, utm_campaign, "
                "ga4) VALUES (%s, 'https://instagram.com/', %s, %s, %s, %s)",
                (order_id, source, medium, campaign, ga4),
            )


def test_utm_report_by_period_source_and_filters(api, rows):
    client = api(MANAGER)
    url = "/reports/utm?from=2026-09-01&to=2026-10-31&group=Month"
    report = client.get(url).json()
    assert [(r["start"], r["orders"], r["total"]) for r in report["rows"]] == [("2026-09-01", 1, 900),
                                                                             ("2026-10-01", 2, 3500)]  # fmt: skip
    assert report["sources"] == ["google", "ig"]

    by_source = client.get(url + "&bySource=true").json()["rows"]
    assert [(r["start"], r["utmSource"], r["total"]) for r in by_source] == [
        ("2026-09-01", "ig", 900), ("2026-10-01", "google", 2000), ("2026-10-01", "ig", 1500),
    ]  # fmt: skip
    assert client.get(url + "&source=ig&campaign=autumn").json()["orders"] == 1
    assert api(STOREKEEPER).get("/reports/utm").status_code == 403


def test_order_remarketing_shows_filled_tags_and_sent_events_only(api, rows):
    client = api(MANAGER)
    data = client.get(f"/orders/{ORDER_ID}/remarketing").json()
    assert data["utm"] == {"first_referrer": "https://instagram.com/", "utm_source": "ig", "utm_medium": "paid",
                           "utm_campaign": "autumn"}  # fmt: skip
    assert [(e["name"], e["sentAt"]) for e in data["events"]] == [("Google Analytics 4", "2026-10-01T12:05:00")]
    assert client.get("/orders/2/remarketing").json()["events"] == []
    assert client.get("/orders/999/remarketing").status_code == 404

    section = client.get(f"/app/reports/orders/{ORDER_ID}/remarketing", headers=HTMX).text
    assert "Google Analytics 4" in section
    assert "Даних немає" in client.get("/app/reports/orders/999/remarketing", headers=HTMX).text
    page = client.get("/app/reports?view=utm&since=2026-09-01&until=2026-10-31&by_source=1&group=2").text
    assert "<td>google</td>" in page
