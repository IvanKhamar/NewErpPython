"""Birthdays, a coupon with an SMS and the contacts export. Its own database: the test changes orders and coupons."""

import asyncio
import re
from datetime import date, datetime

import pytest

from newerp.application.marketing.campaigns import (
    BirthdayOrder,
    BirthdaySmsRequest,
    BulkSmsRequest,
    ContactExportRequest,
    CouponDraft,
    MarketingOptions,
    MarketingService,
)
from newerp.application.sms import SmsSendResult
from newerp.domain.common import DomainError
from newerp.domain.marketing import (
    BirthdayPeriod,
    ContactField,
    ContactRow,
    ContactSource,
    OrderContact,
    build_contacts,
    contacts_csv,
    in_period,
    new_coupon_code,
    next_birthday,
    require_coupon_code,
)
from newerp.domain.sms import BIRTHDAY_MARKER
from tests.seed import MANAGER, STOREKEEPER, insert

PHONE = "380670000002"  # the phone of order 2 in the shared test data
TODAY = date(2026, 12, 28)

# ═══ Rules ═══


@pytest.mark.parametrize(
    ("day", "month", "period", "expected"),
    [
        (28, 12, BirthdayPeriod.TODAY, True),
        (29, 12, BirthdayPeriod.TOMORROW, True),
        (28, 12, BirthdayPeriod.WEEK, True),  # today's are in the week too
        (3, 1, BirthdayPeriod.WEEK, True),  # the week runs over New Year
        (5, 1, BirthdayPeriod.WEEK, False),
        (1, 12, BirthdayPeriod.MONTH, True),
        (31, 4, BirthdayPeriod.ALL, False),  # an impossible date
    ],
)
def test_in_period(day, month, period, expected):
    assert in_period(day, month, period, TODAY) == expected


def test_next_moves_february_29_to_the_last_day_in_common_year():
    assert next_birthday(29, 2, TODAY) == date(2027, 2, 28)
    assert next_birthday(3, 1, TODAY) == date(2027, 1, 3)


def test_coupon_code_has_prefix_and_hex_and_is_validated():
    assert re.fullmatch("DN[0-9A-F]{8}", new_coupon_code())
    assert require_coupon_code(" dnabc123 ") == "DNABC123"
    with pytest.raises(DomainError):
        require_coupon_code("DN-1")


def test_build_deduplicates_clean_values_and_takes_name_from_latest_order():
    orders = [
        OrderContact("+38 (067) 111-22-33", "old@test.ua", "Оля", "Стара", "Київ", datetime(2025, 1, 1)),
        OrderContact("0671112233", "OLGA@test.ua", " Ольга ", "Нова", "Львів", datetime(2026, 1, 1)),
    ]

    rows = build_contacts(ContactField.PHONE, ["0671112233", "+380671112233", "12345", "380501234567"], orders)

    assert rows == [
        ContactRow("", "", "", "380501234567", ""),
        ContactRow("Ольга", "Нова", "Львів", "380671112233", "olga@test.ua"),
    ]


def test_build_by_email_skips_invalid():
    rows = build_contacts(ContactField.EMAIL, ["a@b.ua", "A@B.ua", "not-an-email", "x@localhost"], [])
    assert [r.email for r in rows] == ["a@b.ua"]


def test_csv_has_header_and_escapes_formulas_and_separators():
    csv = contacts_csv(ContactField.EMAIL, [ContactRow("=HYPERLINK(1)", "Ко;ва", "", "380671112233", "a@b.ua")])
    assert csv == "Ім'я;Прізвище;Місто;E-mail;Телефон\r\n'=HYPERLINK(1);\"Ко;ва\";;a@b.ua;380671112233\r\n"


# ═══ Service ═══


class FakeStore:
    def __init__(self) -> None:
        self.birthday_rows: list[BirthdayOrder] = []
        self.sms_phones: list[str] = []
        self.sms_since = None
        self.coupons: list[CouponDraft] = []
        self.phones: list[tuple[int, str]] = []
        self.last_order_ids = None
        self.values: list[str] = []

    async def birthday_orders(self):
        return self.birthday_rows

    async def birthday_sms_phones(self, since):
        self.sms_since = since
        return self.sms_phones

    async def coupon_exists(self, code):
        return any(c.code == code for c in self.coupons)

    async def add_coupon(self, coupon):
        self.coupons.append(coupon)
        return 7

    async def order_phones(self):
        return self.phones

    async def orders(self, order_ids):
        self.last_order_ids = order_ids
        return []

    async def contact_values(self, request):
        return self.values

    async def order_contacts(self):
        return []


class FakeSender:
    is_configured = True

    def __init__(self) -> None:
        self.results: list[SmsSendResult] = []

    async def send(self, phone, text):
        return self.results.pop(0) if self.results else SmsSendResult(sent=True, response="OK")


class FakeLog:
    def __init__(self) -> None:
        self.messages = []

    def add(self, message):
        self.messages.append(message)

    async def save_changes(self):
        pass


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 18, 10, 0)


@pytest.fixture
def parts():
    return FakeStore(), FakeSender(), FakeLog()


@pytest.fixture
def service(parts):
    store, sender, log = parts
    return MarketingService(store, sender, log, MarketingOptions(bulk_sms_delay_milliseconds=0), FixedClock())


def run(coroutine):
    return asyncio.run(coroutine)


def test_birthdays_one_per_phone_from_latest_order_with_pickup_city_and_sent_count(parts, service):
    store, _, _ = parts
    store.birthday_rows += [
        BirthdayOrder("0671112233", "Оля", "Стара", "", "Київ", "Нова Пошта", "", 1, 1, datetime(2025, 1, 1)),
        BirthdayOrder(
            "+38 067 111 22 33", "Ольга", "Нова", "", "", "Самовивіз з магазину", "@olga", 18, 10, datetime(2026, 5, 1)
        ),
        BirthdayOrder("0501234567", "Іра", "Інша", "", "Одеса", "", "", 19, 10, datetime(2026, 5, 1)),
    ]
    store.sms_phones += ["380671112233", "+380671112233"]

    [olga] = run(service.birthdays(BirthdayPeriod.TODAY))
    week = run(service.birthdays(BirthdayPeriod.WEEK))

    assert (olga.phone, olga.first_name, olga.city, olga.instagram, olga.sent_this_year) == (
        "380671112233", "Ольга", "Львів", "@olga", 2,
    )  # fmt: skip
    assert [c.first_name for c in week] == ["Ольга", "Іра"]
    assert store.sms_since == datetime(2026, 1, 1)


def test_sms_with_coupon_creates_coupon_then_sends_and_logs_as_birthday(parts, service):
    store, _, log = parts

    result = run(service.send_birthday_sms(BirthdaySmsRequest(
        phone="067 111 22 33", text=" Вітаємо! DNAB12CD34 ", coupon_code="dnab12cd34",
    )))  # fmt: skip

    assert (result.sent, result.coupon_id, result.response) == (True, 7, "OK")
    [coupon] = store.coupons
    assert (coupon.code, coupon.name, coupon.discount_percent, coupon.start, coupon.end) == (
        "DNAB12CD34", "ДН 380671112233 18.10.2026", 20, date(2026, 10, 18), date(2026, 11, 17),
    )  # fmt: skip
    [message] = log.messages
    assert (message.order_id, message.pre_order_id, message.phone, message.text) == (
        0, BIRTHDAY_MARKER, "380671112233", "Вітаємо! DNAB12CD34",
    )  # fmt: skip


def test_sms_rejects_taken_coupon_before_sending(parts, service):
    store, _, log = parts
    store.coupons.append(CouponDraft("DNAB12CD34", "", 20, date.min, date.min, datetime.min))

    with pytest.raises(DomainError):
        run(service.send_birthday_sms(BirthdaySmsRequest(phone="0671112233", text="Текст", coupon_code="DNAB12CD34")))
    assert log.messages == []


def test_bulk_sends_once_per_number_and_reports_bad_numbers(parts, service):
    _, sender, log = parts
    sender.results.append(SmsSendResult(sent=False, response="Помилка"))

    result = run(service.send_bulk(BulkSmsRequest(
        phones=["0671112233", "+380671112233", "123", "0501234567"], text="Вітаємо!",
    )))  # fmt: skip

    assert (result.sent, result.not_sent, result.skipped) == (1, 1, ["123"])
    assert len(log.messages) == 2


def test_customer_orders_match_normalized_phone(parts, service):
    store, _, _ = parts
    store.phones += [(1, "+38 (067) 111-22-33"), (2, "0501234567"), (3, "0671112233")]

    run(service.customer_orders("067-111-22-33"))

    assert store.last_order_ids == [1, 3]


def test_export_needs_source_and_builds_csv(parts, service):
    store, _, _ = parts
    with pytest.raises(DomainError):
        run(service.export_csv(ContactExportRequest(field=ContactField.PHONE, sources=ContactSource.NONE)))
    store.values += ["0671112233", "bad"]

    csv = run(service.export_csv(ContactExportRequest(
        field=ContactField.PHONE, sources=ContactSource.ORDERS | ContactSource.QUESTIONS,
    )))  # fmt: skip

    assert csv == "Ім'я;Прізвище;Місто;Телефон;E-mail\r\n;;;380671112233;\r\n"


# ═══ API ═══


def test_birthday_greeting_with_coupon_is_logged_and_counted(api, db):
    today = datetime.now()
    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE dd_order SET birthday_day = %s, birthday_month = %s WHERE order_id = 2", (today.day, today.month)
        )
    manager = api(MANAGER)

    [contact] = manager.get("/marketing/birthdays?period=Today").json()
    assert (contact["phone"], contact["firstName"], contact["sentThisYear"]) == (PHONE, "Тарас", 0)
    code = manager.post("/marketing/birthdays/coupon-code").json()["code"]
    sent = manager.post(
        "/marketing/birthdays/sms",
        json={"phone": contact["phone"], "text": f"Вітаємо! Промокод {code}", "couponCode": code},
    ).json()
    assert sent["sent"] is False  # in tests the SMS goes to the stub
    assert sent["couponId"] is not None

    with db.cursor() as cursor:
        cursor.execute("SELECT type, discount, uses_total, status FROM dd_coupon WHERE code = %s", (code,))
        assert cursor.fetchone() == ("P", 20, 1, 1)
        cursor.execute("SELECT order_id, phone FROM dd_sms_log WHERE pre_order_id = %s", (BIRTHDAY_MARKER,))
        assert cursor.fetchall() == ((0, PHONE),)
    [contact] = manager.get("/marketing/birthdays?period=Week").json()
    assert contact["sentThisYear"] == 1
    again = manager.post("/marketing/birthdays/sms", json={"phone": PHONE, "text": "Ще раз", "couponCode": code})
    assert again.status_code == 400  # the coupon exists

    [order] = manager.get("/marketing/customers/orders?phone=0670000002").json()
    assert (order["orderId"], order["status"]) == (2, "5) Відправлено")

    assert api(STOREKEEPER).get("/marketing/birthdays").status_code == 403


def test_export_merges_sources_and_fills_names_from_orders(api, db):
    for phone, added in (
        ("+38 (067) 000-00-02", "2026-10-05"),
        ("093 555 11 22", "2026-10-06"),
        ("0931234567", "2025-01-01"),
    ):
        insert(db, "dd_question", name="Гість", telephone=phone, question="?", date_added=added)
    insert(db, "dd_subscribe", email="news@test.ua", date_added="2026-10-05")
    manager = api(MANAGER)

    response = manager.get(
        "/marketing/contacts.csv?field=Phone&sources=Orders&sources=Questions&from=2026-10-01&to=2026-10-31"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.content.startswith(b"\xef\xbb\xbf")  # the BOM, for Excel
    lines = response.content.decode("utf-8-sig").split("\r\n")
    assert lines[0] == "Ім'я;Прізвище;Місто;Телефон;E-mail"
    assert "Тарас;Клієнт2;;380670000002;" in lines
    assert ";;;380935551122;" in lines
    assert not any("380931234567" in line for line in lines)  # outside the period

    emails = manager.get("/marketing/contacts.csv?field=Email&sources=Subscribers").content.decode("utf-8-sig")
    assert ";;;news@test.ua;" in emails
    assert manager.get("/marketing/contacts.csv?field=Email").status_code == 400
