"""Low-stock alerts: once a day an SMS lists the consumables that fell below their minimum since the last check.

Only items that crossed the minimum in that time are named, from the movement log, so the same item does not come
again every day; the "below minimum" filter and the reorder report show all of them. Python first."""

from dataclasses import dataclass
from datetime import timedelta

from newerp.application.common import Clock, Dto
from newerp.application.sms import SmsSender
from newerp.application.stock.ports import StockItemFilter, StockRepository
from newerp.domain.sms import MAX_LENGTH, normalize_phone
from newerp.domain.stock import LowStockLine, low_stock_text


@dataclass(frozen=True)
class LowStockAlertOptions:
    phones: tuple[str, ...]  # who gets the SMS (the storekeeper); empty: no alerts
    period_hours: int = 24  # how far back the movement log is read: the time between two runs


class LowStockAlertResult(Dto):
    items: int  # consumables that fell below the minimum in the period
    sent: int  # SMS the service accepted
    phones: int


class LowStockAlertService:
    def __init__(self, repository: StockRepository, sender: SmsSender, options: LowStockAlertOptions, clock: Clock):
        self._repository = repository
        self._sender = sender
        self._options = options
        self._clock = clock

    async def send(self) -> LowStockAlertResult:
        since = self._clock.now() - timedelta(hours=self._options.period_hours)
        fell = set(await self._repository.fell_below_minimum(since))
        phones = [normalize_phone(phone) for phone in self._options.phones]
        if not fell or not phones:
            return LowStockAlertResult(items=len(fell), sent=0, phones=len(phones))
        items = [i for i in await self._repository.list_items(StockItemFilter(below_minimum_only=True)) if i.id in fell]
        text = low_stock_text([LowStockLine(i.name, i.quantity, i.min_quantity, i.unit) for i in items], MAX_LENGTH)
        sent = 0
        for phone in phones:
            if (await self._sender.send(phone, text)).sent:
                sent += 1
        return LowStockAlertResult(items=len(items), sent=sent, phones=len(phones))
