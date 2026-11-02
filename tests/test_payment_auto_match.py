"""Automatic matching of bank payments: only one confident order is linked without a person. Its own database."""

import asyncio
from datetime import date, datetime, time
from decimal import Decimal

from newerp.application.common import SystemClock
from newerp.application.payments import BankPaymentService, BankTransaction
from newerp.domain.payments import (
    BankPayment,
    MatchReason,
    OrderCandidate,
    ScoredCandidate,
    confident_match,
    is_confident,
)
from tests.seed import MANAGER, STOREKEEPER, insert


def candidate(order_id: int, reasons: MatchReason, linked: int | None = None) -> ScoredCandidate:
    order = OrderCandidate(order_id, "Тарас Клієнт", "0670000002", Decimal(2000), Decimal(0),
                           datetime(2026, 10, 1), "Нове", linked)  # fmt: skip
    return ScoredCandidate(order, reasons, 0)


def test_a_confident_order_is_named_and_the_sum_fits():
    assert is_confident(candidate(5, MatchReason.ORDER_NUMBER | MatchReason.TOTAL))
    assert is_confident(candidate(5, MatchReason.PHONE | MatchReason.PREPAYMENT))
    assert not is_confident(candidate(5, MatchReason.NAME | MatchReason.TOTAL))  # a namesake with the same sum
    assert not is_confident(candidate(5, MatchReason.ORDER_NUMBER))  # the sum differs
    assert not is_confident(candidate(5, MatchReason.PHONE | MatchReason.TOTAL, linked=7))  # already paid


def test_only_exactly_one_confident_order_is_linked():
    sure = candidate(5, MatchReason.ORDER_NUMBER | MatchReason.TOTAL)
    other = candidate(6, MatchReason.PHONE | MatchReason.TOTAL)
    weak = candidate(7, MatchReason.NAME)
    assert confident_match([sure, weak]).order_id == 5
    assert confident_match([sure, other]) is None  # two: a question for a person
    assert confident_match([weak]) is None


def test_auto_match_links_older_payments_with_one_confident_order(api, db):
    # 2000 with the phone of order 2 in the purpose: confident. The seeded payment (surname and sum) is not.
    insert(
        db, "dd_privatbank", appcode="tx-phone", card="UA00", amount="2000.00 UAH", cardamount="2000.00 UAH",
        rest="7000.00 UAH", description="Оплата від 0670000002", terminal="", trandate=date(2026, 10, 4),
        trantime="11:00:00", status="", show_status=True, order_id=0,
    )  # fmt: skip
    client = api(MANAGER)
    result = client.post("/payments/bank/auto-match?from=2026-10-01&to=2026-10-31").json()

    assert result["checked"] == 2
    [link] = result["links"]
    assert link["orderId"] == 2
    [still_open] = client.get("/payments/bank?linked=false").json()["items"]
    assert still_open["payer"] == "Тарас Клієнт2"
    # Now order 2 is paid, so the seeded payment would not be confident even with its phone.
    assert client.post("/payments/bank/auto-match?from=2026-10-01&to=2026-10-31").json()["links"] == []
    assert "Перевірено платежів без замовлення: 1" in client.post("/app/payments/auto-match").text
    assert api(STOREKEEPER).post("/payments/bank/auto-match").status_code == 403


class Bank:
    is_configured = True

    async def fetch(self, from_: date, to: date) -> list[BankTransaction]:
        return [BankTransaction("tx-new", "UA00", date(2026, 10, 5), time(9, 0), Decimal(1500), None, True,
                                "Замовлення 12345", "Олена", "", "", "")]  # fmt: skip


class Repository:
    def __init__(self) -> None:
        self.added: list[BankPayment] = []

    async def existing_app_codes(self, app_codes: list[str]) -> set[str]:
        return set()

    def add(self, payment: BankPayment) -> None:
        payment.id = len(self.added) + 1
        self.added.append(payment)

    async def save_changes(self) -> None:
        return None

    async def find_candidates(self, query) -> list[OrderCandidate]:
        return [OrderCandidate(12345, "Олена Петренко", "0500000000", Decimal(1500), Decimal(0),
                               datetime(2026, 10, 4), "Нове", None)]  # fmt: skip


def test_import_links_a_new_payment_at_once():
    repository = Repository()
    service = BankPaymentService(repository, Bank(), SystemClock())
    result = asyncio.run(service.import_statement(date(2026, 10, 4), date(2026, 10, 5)))
    assert (result.added, result.auto_linked) == (1, 1)
    assert repository.added[0].order_id == 12345
