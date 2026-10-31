"""Product order by sales. Its own database: the test adds products, a label and orders."""

import asyncio
from datetime import date, datetime, timedelta

import pytest

from newerp.application.catalog.sort import (
    ApplySortRequest,
    CrossSellMetricDto,
    ProductSortOptions,
    ProductSortService,
    SalesPeriod,
    SetPinRequest,
    SortProductDto,
    SortResult,
)
from newerp.application.common import NotFoundError
from newerp.domain.catalog import (
    CROSS_SELL_KEY,
    SortGroup,
    SortGroupType,
    SortPin,
    by_sales,
    code_to_type,
    merge,
    type_to_code,
)
from newerp.domain.common import DomainError
from tests.seed import COMPLETED, LANGUAGE_ID, MANAGER, STOREKEEPER, insert, order, product

# ═══ Sorter ═══


def test_by_sales_puts_best_sellers_first_and_keeps_original_order_for_ties():
    assert by_sales([1, 2, 3, 4, 5], {3: 5, 4: 9, 5: 5}) == [4, 3, 5, 1, 2]


def test_merge_puts_pins_on_their_positions_and_fills_the_rest_from_queue():
    assert merge([10, 20, 30, 40], [SortPin(1, 30), SortPin(3, 99)]) == [30, 10, 99, 20, 40]


def test_merge_keeps_pins_beyond_the_list_at_the_end():
    assert merge([10, 20], [SortPin(10, 7), SortPin(5, 8)]) == [10, 20, 8, 7]


def test_type_code_round_trips():
    for group_type in SortGroupType:
        assert code_to_type(type_to_code(group_type)) == group_type


# ═══ Service ═══

SHIRTS = SortGroup(SortGroupType.CATEGORY, "5", "Сорочки", category_id=5)
NEW = SortGroup(SortGroupType.LABEL, "Новинка", "Мітка: Новинка", label_id=1)
PANEL = SortGroup(SortGroupType.CROSS_SELL, CROSS_SELL_KEY, "Допродаж")
NOW = datetime(2026, 10, 18, 12, 0)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class FakeStore:
    def __init__(self) -> None:
        self.groups = [SHIRTS, NEW, PANEL]
        self.members = {SHIRTS: [1, 2, 3], NEW: [3, 1, 2], PANEL: [2, 3]}
        self.pin_rows: dict[SortGroup, list[SortPin]] = {}
        self.sold = {2: 10, 3: 4}
        self.sort_orders: dict[int, int] = {}
        self.applied: list[tuple[bool, list[int]]] = []
        self.snapshots: list[str] = []
        self.last_range: tuple[datetime, datetime] | None = None
        self.last_cross_sell_only: bool | None = None
        self.panel_only = False

    async def list_groups(self):
        return self.groups

    async def group_product_ids(self, group):
        return self.members.get(group, [])

    async def sales(self, product_ids, start, end, cross_sell_only):
        self.last_range = (start, end)
        self.last_cross_sell_only = cross_sell_only
        return {i: n for i, n in self.sold.items() if i in product_ids}

    async def products(self, product_ids, cross_sell):
        return [
            SortProductDto(product_id=i, name=f"Товар {i}", image="", sort_order=self.sort_orders.get(i, 0))
            for i in product_ids
            if i < 100
        ]

    async def apply(self, cross_sell, ordered_product_ids):
        self.applied.append((cross_sell, list(ordered_product_ids)))
        return len(ordered_product_ids)

    async def pins(self, group):
        return self.pin_rows.get(group, [])

    async def set_pin(self, group, position, product_id, now):
        kept = [p for p in self.pin_rows.get(group, []) if p.product_id != product_id and p.position != position]
        self.pin_rows[group] = [*kept, SortPin(position, product_id)]

    async def create_snapshot(self, note, now):
        self.snapshots.append(note)
        return len(self.snapshots)

    async def cross_sell_panel_only(self):
        return self.panel_only

    async def set_cross_sell_panel_only(self, panel_only):
        self.panel_only = panel_only

    async def search_products(self, term, limit):
        return [SortProductDto(product_id=1, name=term, image="", sort_order=0)]


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def service(store) -> ProductSortService:
    return ProductSortService(store, ProductSortOptions(default_days=30), FixedClock())


def run(coroutine):
    return asyncio.run(coroutine)


def test_preview_sorts_by_sales_with_pins_and_current_positions(store, service):
    store.pin_rows[SHIRTS] = [SortPin(1, 1)]
    store.sort_orders = {1: 3, 2: 1, 3: 2}

    rows = run(service.preview(SortGroupType.CATEGORY, "5", None))

    assert [r.product_id for r in rows] == [1, 2, 3]
    assert [(r.pinned, r.sales, r.current_position) for r in rows] == [(True, 0, 3), (False, 10, 1), (False, 4, 2)]
    assert store.last_range == (NOW - timedelta(days=30), NOW)


def test_preview_uses_date_range_inclusive(store, service):
    run(service.preview(SortGroupType.CATEGORY, "5", SalesPeriod(from_=date(2026, 10, 1), to=date(2026, 10, 10))))

    start, end = store.last_range
    assert start == datetime(2026, 10, 1)
    assert end.date() == date(2026, 10, 10)
    assert end.hour == 23


@pytest.mark.parametrize(
    "period",
    [SalesPeriod(from_=date(2026, 10, 1)), SalesPeriod(from_=date(2026, 10, 9), to=date(2026, 10, 1))],
)
def test_preview_rejects_half_or_reversed_range(service, period):
    with pytest.raises(DomainError):
        run(service.preview(SortGroupType.CATEGORY, "5", period))


def test_new_label_keeps_newest_first_not_sales(service):
    rows = run(service.preview(SortGroupType.LABEL, "Новинка", None))
    assert [r.product_id for r in rows] == [3, 1, 2]


def test_unknown_group_is_not_found(service):
    with pytest.raises(NotFoundError):
        run(service.preview(SortGroupType.CATEGORY, "999", None))


def test_apply_snapshots_and_writes_but_not_for_cross_sell(store, service):
    result = run(service.apply(ApplySortRequest(type=SortGroupType.CATEGORY, key="5", product_ids=[3, 2, 1])))
    panel = run(service.apply(ApplySortRequest(type=SortGroupType.CROSS_SELL, key=CROSS_SELL_KEY, product_ids=[3, 2])))

    assert result == SortResult(snapshot_id=1, updated=3)
    assert panel == SortResult(snapshot_id=None, updated=2)
    assert store.applied == [(False, [3, 2, 1]), (True, [3, 2])]


@pytest.mark.parametrize("ids", [[], [1, 1]])
def test_apply_rejects_empty_or_duplicates(store, service, ids):
    with pytest.raises(DomainError):
        run(service.apply(ApplySortRequest(type=SortGroupType.CATEGORY, key="5", product_ids=ids)))
    assert store.applied == []


def test_recalculate_all_takes_one_snapshot_and_applies_every_group(store, service):
    result = run(service.recalculate_all(SalesPeriod(days=7)))

    assert result == SortResult(snapshot_id=1, updated=8)
    assert len(store.snapshots) == 1
    assert len(store.applied) == 3
    assert store.last_range[0] == NOW - timedelta(days=7)


def test_cross_sell_panel_only_metric_passes_flag_to_sales(store, service):
    run(service.set_cross_sell_metric(CrossSellMetricDto(panel_only=True)))

    run(service.preview(SortGroupType.CROSS_SELL, CROSS_SELL_KEY, None))
    assert store.last_cross_sell_only is True
    run(service.preview(SortGroupType.CATEGORY, "5", None))
    assert store.last_cross_sell_only is False


def test_set_pin_validates_position_and_product(store, service):
    for request in (
        SetPinRequest(type=SortGroupType.CATEGORY, key="5", position=0, product_id=1),
        SetPinRequest(type=SortGroupType.CATEGORY, key="5", position=1, product_id=404),
    ):
        with pytest.raises(DomainError):
            run(service.set_pin(request))

    run(service.set_pin(SetPinRequest(type=SortGroupType.CATEGORY, key="5", position=2, product_id=3)))

    assert store.pin_rows[SHIRTS] == [SortPin(2, 3)]


def test_search_needs_two_characters(service):
    assert run(service.search_products(" a ")) == []
    assert len(run(service.search_products("Тов"))) == 1


# ═══ API ═══

CATEGORY = 30
PERIOD = "from=2026-10-01&to=2026-10-10"


def add_catalog(db) -> None:
    for product_id in (201, 202, 203):
        product(db, product_id, f"D-{product_id}", 1000)
        insert(db, "dd_product_to_category", product_id=product_id, category_id=CATEGORY)
        insert(
            db, "dd_product_description", product_id=product_id, language_id=LANGUAGE_ID, name=f"Сукня {product_id}",
            description="", tag="", meta_title="", meta_description="", meta_keyword="",
        )  # fmt: skip
    with db.cursor() as cursor:
        cursor.execute("UPDATE dd_product SET sort_order = 0 WHERE product_id > 200")
    insert(
        db, "dd_category_description", category_id=CATEGORY, language_id=LANGUAGE_ID, name="Сукні", description="",
        meta_title="", meta_description="", meta_keyword="",
    )  # fmt: skip
    insert(db, "dd_product_label", label_id=1, name="Хіт", background_color="", text_color="", status=True)
    insert(db, "dd_product_to_label", product_id=201, label_id=1)
    insert(db, "dd_product_to_label", product_id=202, label_id=1)

    added = datetime(2026, 10, 5, 12)
    order(db, 100, COMPLETED, 4000, added)
    order(db, 101, COMPLETED, 50000, added, comment="Замовлення б2б")  # wholesale: does not count
    order(db, 102, COMPLETED, 50000, added, user_id=13)  # made by an excluded admin user
    for line_id, order_id, product_id, quantity in ((100, 100, 201, 1), (101, 100, 202, 3), (102, 101, 203, 50),
                                                    (103, 102, 203, 50)):  # fmt: skip
        insert(
            db, "dd_order_product", order_product_id=line_id, order_id=order_id, product_id=product_id, name="Сукня",
            model="D", quantity=quantity, price=1000, total=1000 * quantity, tax=0, reward=0,
        )  # fmt: skip


def sort_orders(db) -> list[tuple[int, int]]:
    with db.cursor() as cursor:
        cursor.execute("SELECT product_id, sort_order FROM dd_product WHERE product_id > 200 ORDER BY product_id")
        return [tuple(row) for row in cursor.fetchall()]


def test_category_sorts_by_sales_with_pins_and_restores_snapshot(api, db):
    add_catalog(db)
    manager = api(MANAGER)

    def preview():
        return manager.get(f"/catalog/sort/preview?type=Category&key=30&{PERIOD}").json()

    groups = manager.get("/catalog/sort/groups").json()
    assert {"type": 1, "key": "30", "name": "Сукні"} in groups
    assert {"type": 3, "key": "Хіт", "name": "Мітка: Хіт"} in groups
    assert groups[-1]["type"] == SortGroupType.CROSS_SELL

    assert [(r["productId"], r["sales"]) for r in preview()] == [(202, 3), (201, 1), (203, 0)]
    pin = {"type": SortGroupType.CATEGORY, "key": "30", "position": 1, "productId": 203}
    assert manager.post("/catalog/sort/pins", json=pin).status_code == 204
    rows = preview()
    assert [r["productId"] for r in rows] == [203, 202, 201]
    assert rows[0]["pinned"] is True

    applied = manager.post(
        "/catalog/sort/apply", json={"type": 1, "key": "30", "productIds": [r["productId"] for r in rows]}
    ).json()
    assert applied["updated"] == 3
    assert sort_orders(db) == [(201, 3), (202, 2), (203, 1)]
    assert manager.post(f"/catalog/sort/snapshots/{applied['snapshotId']}/restore").status_code == 204
    assert sort_orders(db) == [(201, 0), (202, 0), (203, 0)]

    [pinned] = manager.get("/catalog/sort/pins").json()
    assert (pinned["productName"], pinned["position"]) == ("Сукня 203", 1)
    assert manager.delete(f"/catalog/sort/pins/{pinned['id']}").status_code == 204

    assert manager.put("/catalog/sort/crosssell-metric", json={"panelOnly": True}).status_code == 200
    assert manager.get("/catalog/sort/crosssell-metric").json() == {"panelOnly": True}
    everything = manager.post("/catalog/sort/recalculate-all", json={"from": "2026-10-01", "to": "2026-10-10"})
    assert everything.json()["updated"] > 0
    # The label comes after the categories, so its order overrides the category one: 202 and 201 lead the label.
    assert sort_orders(db) == [(201, 2), (202, 1), (203, 3)]

    assert api(STOREKEEPER).get("/catalog/sort/groups").status_code == 403
