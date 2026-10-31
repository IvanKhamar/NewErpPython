"""Gift vouchers (like VoucherControllerTest in the Java version). Its own database: the tests change vouchers and an
order. ORDER_ID is not paid yet; order 2 is in "5) Відправлено", which counts as paid. The vouchers are invented."""

import re
from decimal import Decimal

import pytest

from newerp.domain.common import DomainError
from newerp.domain.marketing import (
    GiftVoucher,
    new_voucher_code,
    require_redeemable,
    voucher_balance,
    voucher_usable,
)
from tests.seed import ADMIN, CATEGORY_PRODUCT_ID, LANGUAGE_ID, MANAGER, ORDER_ID, STOREKEEPER, insert
from tests.test_new_orders import request as order_request

PAID_ORDER_ID = 2

# ═══ Rules ═══


def test_of_cleans_and_checks_the_form():
    voucher = GiftVoucher.of(
        " vidro2026 ", None, None, " Олена ", "", None, " З днем народження! ", Decimal("1000.50"), None
    )
    assert voucher == GiftVoucher("VIDRO2026", "", "", "Олена", "", 0, "З днем народження!", Decimal("1000.50"), True)

    def error(*args) -> str:
        with pytest.raises(DomainError) as raised:
            GiftVoucher.of(*args)
        return str(raised.value)

    assert error("TOO-LONG-CODE", "", "", "Олена", "", 0, "", Decimal(10), True) == (
        "Код сертифіката — від 1 до 10 латинських літер або цифр."
    )
    error("ПОДАРУНОК", "", "", "Олена", "", 0, "", Decimal(10), True)
    amount_error = "Сума сертифіката — більше 0, не більше двох знаків після коми."
    assert error("ABC", "", "", "Олена", "", 0, "", Decimal(0), True) == amount_error
    assert error("ABC", "", "", "Олена", "", 0, "", Decimal("10.005"), True) == amount_error
    assert error("ABC", "Іван", "", " ", "", 0, "", Decimal(10), True) == "Вкажіть, кому сертифікат."


def test_new_code_avoids_look_alikes():
    assert re.fullmatch("[A-HJ-NP-Z2-9]{8}", new_voucher_code())


def test_a_spending_fits_the_balance_and_what_the_order_still_needs():
    require_redeemable(Decimal(500), Decimal(650), Decimal(500), True)
    for amount, balance, left, usable, message in (
        (700, 650, 900, True, "На сертифікаті лише 650 грн."),
        (600, 650, 500, True, "Це більше, ніж лишилось сплатити за замовлення (500 грн)."),
        (0, 650, 500, True, "Сума списання"),
        (10, 650, 500, False, "Сертифікат зараз"),
    ):
        with pytest.raises(DomainError, match=re.escape(message)):
            require_redeemable(Decimal(amount), Decimal(balance), Decimal(left), usable)


def test_balance_and_usability():
    assert voucher_balance(Decimal(1000), Decimal(-350)) == 650
    assert voucher_balance(Decimal(1000), None) == 1000
    assert voucher_usable(True, 0, False)  # made in the admin, there is no order behind it
    assert not voucher_usable(True, 2001, False)  # bought in an order that is not paid yet
    assert voucher_usable(True, 2001, True)
    assert not voucher_usable(False, 0, True)


# ═══ API ═══


@pytest.fixture
def vouchers(db):
    """Like vouchers.sql in Java: each test starts from the same vouchers and an empty SMS log."""
    with db.cursor() as cursor:
        for table in (
            "dd_voucher",
            "dd_voucher_history",
            "dd_voucher_theme",
            "dd_voucher_theme_description",
            "dd_sms_log",
        ):
            cursor.execute(f"DELETE FROM {table}")
    insert(db, "dd_voucher_theme", voucher_theme_id=1, image="catalog/demo/canon_eos_5d_2.jpg")
    insert(db, "dd_voucher_theme_description", voucher_theme_id=1, language_id=LANGUAGE_ID, name="Свято")
    insert(db, "dd_voucher_theme_description", voucher_theme_id=1, language_id=1, name="Holiday")
    for voucher_id, order_id, code, sender, receiver, theme, amount, active, added, message in (
        # made in the admin, partly spent in the order ORDER_ID
        (1, 0, "GIFT1000", "Магазин", "Олена", 1, 1000, True, "2026-09-01 10:00", "З днем народження!"),
        (2, ORDER_ID, "SHOP500", "Ірина", "Марія", 0, 500, True, "2026-10-01 10:00", ""),  # its order is not paid
        (3, PAID_ORDER_ID, "SHOP700", "Петро", "Андрій", 0, 700, True, "2026-10-05 12:00", ""),  # bought and paid
        (4, 0, "OFF200", "Магазин", "Тарас", 0, 200, False, "2026-08-01 10:00", ""),
    ):
        insert(
            db, "dd_voucher", voucher_id=voucher_id, order_id=order_id, code=code, from_name=sender, from_email="",
            to_name=receiver, to_email="", voucher_theme_id=theme, message=message, amount=amount, status=active,
            date_added=added,
        )  # fmt: skip
    insert(db, "dd_voucher_history", voucher_history_id=1, voucher_id=1, order_id=ORDER_ID, amount=-350,
           date_added="2026-10-01 10:00")  # fmt: skip


def test_list_and_card_with_balance_usability_and_history(api, vouchers):
    admin = api(ADMIN)

    cards = admin.get("/marketing/vouchers").json()
    assert [(v["id"], v["code"], v["usable"]) for v in cards] == [
        (4, "OFF200", False), (3, "SHOP700", True), (2, "SHOP500", False), (1, "GIFT1000", True),
    ]  # fmt: skip
    assert cards[3]["balance"] == 650
    assert [v["id"] for v in admin.get("/marketing/vouchers?search=олена").json()] == [1]

    card = admin.get("/marketing/vouchers/1").json()
    assert (card["code"], card["themeName"], card["amount"], card["balance"]) == ("GIFT1000", "Свято", 1000, 650)
    [spent] = card["history"]
    assert (spent["orderId"], spent["customer"], spent["amount"]) == (ORDER_ID, f"Тарас Клієнт{ORDER_ID}", -350)
    assert admin.get("/marketing/vouchers/99").status_code == 404
    assert admin.get("/marketing/vouchers/themes").json() == [{"id": 1, "name": "Свято"}]


def test_create_generates_a_code_and_refuses_duplicates_and_bad_amounts(api, vouchers):
    admin = api(ADMIN)

    voucher = admin.post("/marketing/vouchers", json={"toName": "Софія", "amount": 1500}).json()

    assert re.fullmatch("[A-HJ-NP-Z2-9]{8}", voucher["code"])
    assert (voucher["orderId"], voucher["balance"], voucher["active"], voucher["usable"]) == (0, 1500, True, True)
    duplicate = {"code": "gift1000", "toName": "Софія", "amount": 100}
    assert admin.post("/marketing/vouchers", json=duplicate).status_code == 400
    assert admin.post("/marketing/vouchers", json={"toName": "Софія", "amount": 0}).status_code == 400
    # A bigger voucher sum does not touch the history: the balance follows the sum.
    updated = admin.put("/marketing/vouchers/1", json={"code": "GIFT1000", "toName": "Олена", "amount": 1200})
    assert updated.json()["balance"] == 850


def test_only_an_unspent_voucher_is_deleted_and_only_a_usable_one_is_texted(api, db, vouchers):
    admin = api(ADMIN)
    assert admin.delete("/marketing/vouchers/1").status_code == 400
    assert admin.delete("/marketing/vouchers/4").status_code == 204

    sent = admin.post("/marketing/vouchers/1/sms", json={"phone": "+38 (067) 111-22-33"}).json()

    assert sent["sent"] is False  # the stub
    with db.cursor() as cursor:
        cursor.execute("SELECT phone, text FROM dd_sms_log")
        [(phone, text)] = cursor.fetchall()
    assert phone == "380671112233"
    assert "650" in text and "GIFT1000" in text
    not_paid = admin.post("/marketing/vouchers/2/sms", json={"phone": "380671112233"})
    assert not_paid.status_code == 400


def test_storekeeper_has_no_access(api):
    assert api(STOREKEEPER).get("/marketing/vouchers").status_code == 403


def test_a_gift_voucher_pays_part_of_the_order_and_stays_when_the_lines_change(api):
    client = api(MANAGER)
    # One shirt for 900, discount 100, advance 200: 600 is left to pay.
    body = order_request("+380501112277", quantity=1) | {"firstname": "Софія", "lastname": "Сертифікатна"}
    order_id = client.post("/orders", json=body).json()["orderId"]
    code = client.post("/marketing/vouchers", json={"toName": "Софія", "amount": 500}).json()["code"]

    spent = client.post(f"/orders/{order_id}/voucher", json={"code": code.lower()}).json()

    assert (spent["code"], spent["amount"], spent["orderTotal"], spent["voucherBalance"]) == (code, 500, 300, 0)
    assert client.get(f"/orders/{order_id}/voucher").json() == spent
    again = client.post(f"/orders/{order_id}/voucher", json={"code": code})
    assert again.status_code == 400  # one per order

    # Three shirts and shipping: 2670 minus the voucher.
    lines = [{"productId": CATEGORY_PRODUCT_ID, "productOptionValueId": 502, "quantity": 3, "price": 900}]
    changed = client.put(f"/orders/{order_id}/composition", json={
        "lines": lines, "shippingCost": 70, "discount": 100, "prepayment": 500,
    })  # fmt: skip
    assert changed.json()["total"] == 2170
    assert client.get(f"/orders/{order_id}").json()["total"] == 2170
    # Cheaper than what the voucher paid: refused, the order stays as it was.
    cheaper = [{"productId": CATEGORY_PRODUCT_ID, "productOptionValueId": 502, "quantity": 1, "price": 400}]
    assert client.put(f"/orders/{order_id}/composition", json={"lines": cheaper}).status_code == 400

    assert client.delete(f"/orders/{order_id}/voucher").status_code == 204
    assert client.get(f"/orders/{order_id}").json()["total"] == 2670
    assert client.get(f"/orders/{order_id}/voucher").status_code == 204
    [voucher] = client.get(f"/marketing/vouchers?search={code}").json()
    assert voucher["balance"] == 500
