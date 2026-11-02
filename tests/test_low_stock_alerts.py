"""Low-stock alerts: the SMS text, who gets it, the movement log decides what is new, the beat task, the home panel."""

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Annotated

import pytest
from fastapi import Depends

from newerp import worker
from newerp.api.stock import get_low_stock_alerts, get_stock_repository
from newerp.application.common import SystemClock
from newerp.application.sms import SmsSendResult
from newerp.application.stock.alerts import LowStockAlertOptions, LowStockAlertService
from newerp.config import CheckboxSettings, NovaPoshtaSettings, Settings, StockSettings
from newerp.domain.stock import LowStockLine, low_stock_text
from newerp.infrastructure.stock.repositories import SqlStockRepository
from newerp.main import app
from tests.helpers import create
from tests.seed import MANAGER, STOREKEEPER

PHONES = ("+380 (67) 000-00-01", "0670000002")


def line(name: str, quantity: int = 2, minimum: int = 10) -> LowStockLine:
    return LowStockLine(name, Decimal(quantity), Decimal(minimum), "м")


def test_text_lists_the_items_and_counts_what_does_not_fit():
    assert low_stock_text([line("Льон", 12, 30), line("Нитки", 3)], 670) == (
        "Мало на складі: Льон 12 м (мін. 30); Нитки 3 м (мін. 10)"
    )
    text = low_stock_text([line(f"Тканина {n:02}") for n in range(40)], 120)
    assert len(text) <= 120
    assert text.endswith(" поз.")
    assert text.startswith("Мало на складі: Тканина 00 2 м (мін. 10); Тканина 01")


class Sender:
    is_configured = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, phone: str, text: str) -> SmsSendResult:
        self.sent.append((phone, text))
        return SmsSendResult(sent=True, response="ok")


@pytest.fixture
def sender():
    """The endpoint's service with test phones and a sender that records the SMS instead of sending them."""
    fake = Sender()

    def service(repository: Annotated[SqlStockRepository, Depends(get_stock_repository)]) -> LowStockAlertService:
        return LowStockAlertService(repository, fake, LowStockAlertOptions(PHONES), SystemClock())

    app.dependency_overrides[get_low_stock_alerts] = service
    yield fake
    app.dependency_overrides.pop(get_low_stock_alerts, None)


def test_only_items_that_fell_below_since_yesterday_are_sent(api, sender):
    client = api(STOREKEEPER)
    fell = create(client, "/stock/items", {"name": "Льон для сповіщення", "unit": "м", "minQuantity": 10})
    always_low = create(client, "/stock/items", {"name": "Ґудзики давно мало", "unit": "шт", "minQuantity": 50})
    client.post(f"/stock/items/{fell}/adjustments", json={"delta": 15, "reason": "Прихід"})
    client.post(f"/stock/items/{fell}/adjustments", json={"delta": -9, "reason": "Пошиття"})  # 15 → 6: crossed
    client.post(f"/stock/items/{always_low}/adjustments", json={"delta": 5, "reason": "Прихід"})  # 0 → 5: no cross

    result = client.post("/stock/alerts/low-stock").json()

    assert (result["items"], result["sent"], result["phones"]) == (1, 2, 2)
    assert sender.sent[0] == ("380670000001", "Мало на складі: Льон для сповіщення 6 м (мін. 10)")
    assert api(MANAGER).post("/stock/alerts/low-stock").status_code == 403

    home = client.get("/app/").text
    assert "Мало на складі" in home
    assert "Ґудзики давно мало" in home  # the panel shows everything below the minimum
    assert "Мало на складі" not in api(MANAGER).get("/app/").text


def test_nothing_is_sent_without_phones():
    class Repository:
        async def fell_below_minimum(self, since: datetime) -> list[int]:
            return [1]

    fake = Sender()
    service = LowStockAlertService(Repository(), fake, LowStockAlertOptions(()), SystemClock())
    result = asyncio.run(service.send())
    assert (result.items, result.sent, fake.sent) == (1, 0, [])


def test_alert_is_scheduled_only_with_phones():
    def schedule(**stock) -> dict:
        settings = Settings(_env_file=None, checkbox=CheckboxSettings(), nova_poshta=NovaPoshtaSettings(),
                            stock=StockSettings(**stock))  # fmt: skip
        return worker.beat_schedule_for(settings)

    assert "stock-low-stock-alert" not in schedule()
    at = schedule(alert_phones=["0670000001"])["stock-low-stock-alert"]["schedule"]
    assert (at.hour, at.minute) == ({8}, {0})
    assert "stock-low-stock-alert" not in schedule(alert_phones=["0670000001"], alert_time="-")
    assert "stock.low_stock_alert" in worker.app.tasks
