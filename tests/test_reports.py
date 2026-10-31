"""Reports and the revenue feed on the seed orders. Its own database, so other tests add no orders."""

import asyncio
from datetime import date, datetime
from decimal import Decimal

import pytest

from newerp.api.reports import get_feed_settings
from newerp.application.reports import ReportFilter, ReportService
from newerp.config import RevenueFeedSettings
from newerp.domain.common import DomainError
from newerp.domain.reports import ReportGrouping, SaleFact, SalesPeriod, change_percent, group_sales, period_start
from newerp.main import app
from tests.seed import (
    CANCELLED,
    CANCELLED_ORDER_ID,
    CATEGORY_ORDER_ID,
    COMPLETED,
    LANGUAGE_ID,
    MANAGER,
    ORDER_ID,
    PENDING,
    PRODUCT_ID,
    STOREKEEPER,
    insert,
)

FEED_TOKEN = "test-feed-token"  # an obviously fake value

# ═══ Rules ═══


def test_week_starts_on_monday_month_and_year_on_the_first_day():
    sunday = date(2026, 10, 18)
    assert period_start(sunday, ReportGrouping.WEEK) == date(2026, 10, 12)
    assert period_start(sunday, ReportGrouping.MONTH) == date(2026, 10, 1)
    assert period_start(sunday, ReportGrouping.YEAR) == date(2026, 1, 1)


def test_periods_are_clipped_to_the_range_empty_ones_only_with_fill_gaps():
    facts = [
        SaleFact(datetime(2026, 11, 20, 10), Decimal(500), 1),
        SaleFact(datetime(2026, 12, 3, 9), Decimal(300), 2),
        SaleFact(datetime(2026, 12, 30, 18), Decimal(200), 1),
        SaleFact(datetime(2027, 2, 1), Decimal(999), 9),  # outside the range
    ]
    rows = group_sales(facts, ReportGrouping.MONTH, date(2026, 11, 15), date(2027, 1, 10), fill_gaps=False)
    assert rows == [
        SalesPeriod(date(2026, 11, 15), date(2026, 11, 30), 1, 1, Decimal(500)),
        SalesPeriod(date(2026, 12, 1), date(2026, 12, 31), 2, 3, Decimal(500)),
    ]
    filled = group_sales(facts, ReportGrouping.MONTH, date(2026, 11, 15), date(2027, 1, 10), fill_gaps=True)
    assert filled[-1] == SalesPeriod(date(2027, 1, 1), date(2027, 1, 10), 0, 0, Decimal(0))
    assert group_sales(facts, ReportGrouping.DAY, date(2026, 12, 2), date(2026, 12, 1), fill_gaps=True) == []


def test_change_percent_rounds_half_away_from_zero_and_needs_a_previous_value():
    assert change_percent(Decimal(150), Decimal(100)) == 50
    assert change_percent(Decimal("0.5"), Decimal(200)) == -100
    assert change_percent(Decimal(1), Decimal(8)) == -88  # -87.5
    assert change_percent(Decimal(5), Decimal(0)) is None


# ═══ Service ═══


class FakeStore:
    def __init__(self, facts: list[SaleFact]) -> None:
        self.facts = facts
        self.customer_ranges = []

    async def sales(self, from_, to_exclusive, status_id, excluded_status_id):
        return [f for f in self.facts if from_ <= f.date_added < to_exclusive]

    async def products(self, from_, to_exclusive, status_id, excluded_status_id, limit):
        return []

    async def new_customers(self, from_, to_exclusive):
        self.customer_ranges.append((from_, to_exclusive))
        return 0


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def test_dashboard_compares_the_month_with_the_same_days_of_the_month_before():
    facts = [
        SaleFact(datetime(2026, 3, 31, 10), Decimal(1000), 1),
        SaleFact(datetime(2026, 3, 30, 10), Decimal(500), 1),
        SaleFact(datetime(2026, 2, 28, 10), Decimal(250), 1),
    ]
    store = FakeStore(facts)
    dashboard = asyncio.run(ReportService(store, FixedClock(datetime(2026, 3, 31, 15))).dashboard())

    assert (dashboard.sales_today.current, dashboard.sales_today.previous) == (1000, 500)
    assert dashboard.sales_today.change_percent == 100
    # 1–31 March against 1–28 February: February is shorter.
    assert (dashboard.sales_month.current, dashboard.sales_month.previous) == (1500, 250)
    assert dashboard.average_check_month.current == 750
    assert store.customer_ranges[1] == (datetime(2026, 2, 1), datetime(2026, 3, 1))
    assert len(dashboard.last30_days) == 30


def test_feed_days_are_limited_and_the_series_ends_today():
    service = ReportService(FakeStore([]), FixedClock(datetime(2026, 10, 24, 9)))

    feed = asyncio.run(service.revenue_feed(7, 366))
    assert [d.day for d in feed.days][-1] == date(2026, 10, 24) and len(feed.days) == 7
    assert (feed.previous_month.from_, feed.previous_month.to) == (date(2026, 9, 1), date(2026, 9, 24))
    for days in (0, 367):
        with pytest.raises(DomainError):
            asyncio.run(service.revenue_feed(days, 366))


def test_report_range_defaults_to_this_month_and_is_checked():
    service = ReportService(FakeStore([]), FixedClock(datetime(2026, 10, 24, 9)))

    report = asyncio.run(service.sales(ReportFilter()))
    assert (report.from_, report.to) == (date(2026, 10, 1), date(2026, 10, 24))
    for bad in (ReportFilter(date(2026, 10, 5), date(2026, 10, 1)), ReportFilter(date(2022, 1, 1), date(2026, 1, 1))):
        with pytest.raises(DomainError):
            asyncio.run(service.sales(bad))


# ═══ API ═══


def test_sales_by_month_without_cancelled_and_unfinished_products_and_dashboard(api):
    manager = api(MANAGER)

    sales = manager.get("/reports/sales?from=2025-01-01&to=2026-10-31&group=Month").json()
    assert [(r["start"], r["total"]) for r in sales["rows"]] == [
        ("2026-08-01", 1200), ("2026-09-01", 900), ("2026-10-01", 3500),
    ]  # fmt: skip
    assert (sales["from"], sales["orders"]) == ("2025-01-01", 4)

    cancelled = manager.get(f"/reports/sales?from=2025-01-01&to=2026-10-31&statusId={CANCELLED}").json()
    assert (cancelled["orders"], cancelled["total"]) == (1, 700)

    products = manager.get("/reports/products?from=2026-10-01&to=2026-10-31").json()
    top = products["rows"][0]
    assert (top["productId"], top["quantity"], top["name"]) == (PRODUCT_ID, 2, "Сукня")

    dashboard = manager.get("/reports/dashboard").json()
    assert len(dashboard["last30Days"]) == 30
    assert set(dashboard["ordersToday"]) == {"current", "previous", "changePercent"}

    assert manager.get("/reports/sales?from=2026-10-05&to=2026-10-01").status_code == 400
    assert manager.get("/reports/sales?group=Quarter").status_code == 400
    assert api(STOREKEEPER).get("/reports/dashboard").status_code == 403


def test_statuses_count_cancelled_but_not_unfinished_orders_and_returns_of_the_period(api, db):
    insert(db, "dd_return_status", return_status_id=1, language_id=LANGUAGE_ID, name="Очікує")
    for order_id, status_id, quantity, added in (
        (ORDER_ID, 1, 1, datetime(2026, 10, 10, 12)),
        (2, 1, 2, datetime(2026, 10, 12, 12)),
        (CATEGORY_ORDER_ID, 3, 1, datetime(2026, 9, 5, 12)),
        (CANCELLED_ORDER_ID, 1, 5, datetime(2024, 3, 5, 12)),  # outside the period
    ):
        insert(
            db, "dd_return", order_id=order_id, product_id=PRODUCT_ID, customer_id=0, firstname="Ірина",
            lastname="Шевчук", email="", telephone="", product="Сукня", model="T-1", quantity=quantity, opened=False,
            return_reason_id=0, return_action_id=0, return_status_id=status_id, comment="", date_ordered=added.date(),
            date_added=added, date_modified=added,
        )  # fmt: skip
    manager = api(MANAGER)

    report = manager.get("/reports/statuses?from=2025-01-01&to=2026-10-31").json()
    assert [(r["statusId"], r["name"], r["orders"], r["total"]) for r in report["orders"]] == [
        (CANCELLED, "Відмінено", 1, 700), (COMPLETED, "5) Відправлено", 1, 2000), (PENDING, "1) Уточнюємо", 3, 3600),
    ]  # fmt: skip
    assert (report["orderCount"], report["total"]) == (5, 6300)
    # Status 3 is only in a return and has no name in the lookup: it is shown by its number.
    assert [(r["statusId"], r["name"], r["returns"], r["quantity"]) for r in report["returns"]] == [
        (1, "Очікує", 2, 3), (3, "#3", 1, 1),
    ]  # fmt: skip
    assert report["returnCount"] == 3

    assert manager.get("/reports/statuses?from=2026-10-05&to=2026-10-01").status_code == 400
    assert api(STOREKEEPER).get("/reports/statuses").status_code == 403


@pytest.fixture
def feed_settings():
    def use(token: str) -> None:
        app.dependency_overrides[get_feed_settings] = lambda: RevenueFeedSettings(token=token)

    yield use
    app.dependency_overrides.pop(get_feed_settings, None)


def test_feed_needs_token_returns_totals_and_series(api, feed_settings):
    feed_settings(FEED_TOKEN)
    client = api()

    assert client.get("/feeds/revenue.json").status_code == 401
    assert client.get("/feeds/revenue.json?token=wrong").status_code == 401

    response = client.get("/feeds/revenue.json?days=7", headers={"X-Feed-Token": FEED_TOKEN})
    assert response.status_code == 200
    feed = response.json()
    assert len(feed["days"]) == 7 and feed["days"][-1]["date"] == date.today().isoformat()
    assert set(feed["month"]) == {"from", "to", "orders", "revenue", "averageCheck"}

    assert client.get(f"/feeds/revenue.json?token={FEED_TOKEN}").status_code == 200
    assert client.get(f"/feeds/revenue.json?token={FEED_TOKEN}&days=1000").status_code == 400


def test_feed_without_configured_token_is_not_found(api, feed_settings):
    feed_settings("")

    assert api().get("/feeds/revenue.json?token=anything").status_code == 404
