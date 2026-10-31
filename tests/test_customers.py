"""Customers. Its own database: the test adds a customer, a group and an address."""

import asyncio
from datetime import datetime
from decimal import Decimal

import pytest

from newerp.application.common import NotFoundError, PagedResult
from newerp.application.customers import (
    AddCustomerNoteRequest,
    CustomerFilter,
    CustomerOrderItem,
    CustomerProfile,
    CustomerService,
)
from newerp.domain.common import DomainError
from newerp.domain.customers import NOTE_MAX_LENGTH, CustomerOrder, CustomerSummary, validate_note
from tests.seed import CANCELLED_ORDER_ID, LANGUAGE_ID, MANAGER, STOREKEEPER, insert

CUSTOMER_ID, GROUP_ID = 1, 1

# ═══ Rules ═══


def test_spent_excludes_cancelled_dates_span_all_orders():
    summary = CustomerSummary.of([
        CustomerOrder(1, datetime(2026, 1, 5), 22, Decimal(1500)),
        CustomerOrder(2, datetime(2026, 3, 1), 19, Decimal(700)),
        CustomerOrder(3, datetime(2026, 2, 10), 24, Decimal(900)),
    ])  # fmt: skip
    assert summary == CustomerSummary(3, 1, Decimal(2400), datetime(2026, 1, 5), datetime(2026, 3, 1))


def test_no_orders_no_dates():
    assert CustomerSummary.of([]) == CustomerSummary(0, 0, Decimal(0), None, None)


def test_note_is_trimmed_empty_and_too_long_rejected():
    assert validate_note("  Просила дзвонити після 18:00 ") == "Просила дзвонити після 18:00"
    for bad in ("   ", "я" * (NOTE_MAX_LENGTH + 1)):
        with pytest.raises(DomainError):
            validate_note(bad)


# ═══ Service ═══


class FakeStore:
    def __init__(self) -> None:
        self.customers = {
            5: CustomerProfile(5, "Олена", "Коваль", "", "+38 (067) 123-45-67", "Роздріб", True, False,
                               datetime(2025, 1, 1), [], []),
        }  # fmt: skip
        self.order_rows = [
            CustomerOrderItem(id=10, date_added=datetime(2026, 1, 1), status_id=22, status_name="Відправлено",
                              total=Decimal(1200), currency_code="UAH", by_phone=False),
            CustomerOrderItem(id=11, date_added=datetime(2026, 2, 1), status_id=19, status_name="Відмінено",
                              total=Decimal(500), currency_code="UAH", by_phone=True),
        ]  # fmt: skip
        self.notes = []
        self.phone_key = None
        self.last_filter = None

    async def list_page(self, customer_filter):
        self.last_filter = customer_filter
        return PagedResult(items=[], page=customer_filter.page, page_size=customer_filter.page_size, total_count=0)

    async def get(self, customer_id):
        return self.customers.get(customer_id)

    async def orders(self, customer_id, phone_key):
        self.phone_key = phone_key
        return self.order_rows

    async def add_note(self, customer_id, comment, now):
        if customer_id not in self.customers:
            return False
        self.notes.append((customer_id, comment, now))
        return True


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 19, 9, 0)


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def service(store):
    return CustomerService(store, FixedClock())


def test_get_searches_orders_by_phone_key_and_computes_stats_and_risk(store, service):
    details = asyncio.run(service.get(5))

    assert store.phone_key == "671234567"
    stats = details.stats
    assert (stats.order_count, stats.cancelled_count, stats.spent, stats.risk_level) == (2, 1, 1200, "Red")
    with pytest.raises(NotFoundError):
        asyncio.run(service.get(6))


def test_add_note_trims_and_stamps_time_unknown_customer_is_not_found(store, service):
    asyncio.run(service.add_note(5, AddCustomerNoteRequest(comment=" Постійна клієнтка ")))

    assert store.notes == [(5, "Постійна клієнтка", datetime(2026, 10, 19, 9, 0))]
    with pytest.raises(NotFoundError):
        asyncio.run(service.add_note(6, AddCustomerNoteRequest(comment="x")))


def test_list_normalizes_paging_and_search(store, service):
    asyncio.run(service.list_page(CustomerFilter(search=" Коваль ", page=0, page_size=1000)))

    assert store.last_filter.search == "Коваль"
    assert store.last_filter.page >= 1 and store.last_filter.page_size <= 100


# ═══ API ═══


def test_card_finds_orders_by_phone_with_risk_and_notes(api, db):
    insert(db, "dd_customer_group_description", customer_group_id=GROUP_ID, language_id=LANGUAGE_ID, name="Роздріб",
           description="")  # fmt: skip
    insert(
        db, "dd_customer", customer_id=CUSTOMER_ID, customer_group_id=GROUP_ID, language_id=LANGUAGE_ID,
        firstname="Олена", lastname="Коваль", email="olena@example.com", telephone="0935554433", fax="", password="",
        salt="", custom_field="", ip="", status=True, safe=False, token="", code="", address_id=7,
        date_added="2024-05-01",
    )  # fmt: skip
    insert(
        db, "dd_address", address_id=7, customer_id=CUSTOMER_ID, firstname="Олена", lastname="Коваль", company="",
        address_1="Відділення №1", address_2="", city="Київ", postcode="", custom_field="",
    )  # fmt: skip
    manager = api(MANAGER)

    [item] = manager.get("/customers?search=Коваль").json()["items"]
    assert (item["id"], item["name"], item["groupName"], item["orderCount"]) == (
        CUSTOMER_ID,
        "Олена Коваль",
        "Роздріб",
        0,
    )
    # A guest order with the same phone, written another way, is in the card too.
    note = {"comment": " Просила дзвонити ввечері "}
    assert manager.post(f"/customers/{CUSTOMER_ID}/notes", json=note).status_code == 204

    card = manager.get(f"/customers/{CUSTOMER_ID}").json()
    [order] = card["orders"]
    assert (order["id"], order["byPhone"], order["statusName"]) == (CANCELLED_ORDER_ID, True, "Відмінено")
    stats = card["stats"]
    assert (stats["orderCount"], stats["cancelledCount"], stats["spent"], stats["riskLevel"]) == (1, 1, 0, "Red")
    [address] = card["addresses"]
    assert (address["city"], address["isDefault"]) == ("Київ", True)
    assert [n["comment"] for n in card["notes"]] == ["Просила дзвонити ввечері"]
    assert manager.post(f"/customers/{CUSTOMER_ID}/notes", json={"comment": " "}).status_code == 400
    assert manager.get("/customers/9999").status_code == 404

    assert api(STOREKEEPER).get("/customers").status_code == 403
