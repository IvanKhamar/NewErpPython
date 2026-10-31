"""Composition root of the Celery worker and beat (the background workers of .NET).

Run: `uv run celery -A newerp.worker worker --beat --pool=solo` (--pool=solo: Windows has no fork).
Celery beat is the scheduler that sends the periodic tasks below, like a BackgroundService with a PeriodicTimer.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from celery import Celery
from celery.schedules import crontab
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from newerp.application.cashbox.services import CashboxService
from newerp.application.catalog.pre_orders import PreOrderService
from newerp.application.common import SystemClock
from newerp.application.competitors import CompetitorService
from newerp.config import get_settings
from newerp.infrastructure.cashbox.checkbox import checkbox_client
from newerp.infrastructure.cashbox.repository import SqlCashboxRepository
from newerp.infrastructure.catalog.pre_orders import SqlPreOrderStore
from newerp.infrastructure.competitors import HttpPageFetcher, SqlCompetitorStore

settings = get_settings()
logger = logging.getLogger(__name__)

app = Celery("newerp", broker=settings.redis_url, backend=settings.redis_url)
app.conf.update(task_acks_late=True, timezone="Europe/Kyiv")


async def with_session[T](work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """One task run: its own engine (a task runs in its own event loop) and one session, like a DI scope."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False, autoflush=False)() as session:
            return await work(session)
    finally:
        await engine.dispose()


@app.task(name="cashbox.auto_close")
def cashbox_auto_close() -> dict:
    """Checks open shifts against Checkbox and closes those whose cashier's close time has come."""

    async def work(session: AsyncSession) -> dict:
        service = CashboxService(SqlCashboxRepository(session), checkbox_client(settings.checkbox), SystemClock())
        return (await service.auto_close()).model_dump(by_alias=True)

    return asyncio.run(with_session(work))


@app.task(name="preorders.settle")
def pre_orders_settle() -> dict:
    """Takes what the storefront sold ahead off the pre-order limits (the old site's cron updatePreOrdersQuantity)."""

    async def work(session: AsyncSession) -> dict:
        service = PreOrderService(SqlPreOrderStore(session, settings.language_id))
        return (await service.settle()).model_dump(by_alias=True)

    return asyncio.run(with_session(work))


@app.task(name="competitors.check")
def competitors_check() -> dict:
    """Reads competitor prices once a day, in the morning before managers start work (CompetitorPriceWorker)."""

    async def work(session: AsyncSession) -> dict:
        async with HttpPageFetcher() as fetcher:
            store = SqlCompetitorStore(session, settings.language_id)
            result = await CompetitorService(store, fetcher, SystemClock()).check_all()
        if result.checked > 0:
            logger.info(
                "Ціни конкурентів: перевірено %s, змінилося %s, не вдалося %s",
                result.checked, result.changed, result.failed,
            )  # fmt: skip
        return result.model_dump(by_alias=True)

    return asyncio.run(with_session(work))


def daily_at(setting: str) -> crontab | None:
    """crontab for "HH:mm" (like a cron line "mm HH * * *"); "-" or a wrong value: no schedule."""
    text = setting.strip()
    if text == "-":
        return None
    try:
        at = datetime.strptime(text, "%H:%M")
    except ValueError:
        logger.error("competitors.check_time «%s» — не час HH:mm; перевірку цін вимкнено", text)
        return None
    return crontab(hour=at.hour, minute=at.minute)


beat_schedule: dict[str, dict] = {}
# With the Checkbox stub there is nothing to close (CashboxAutoCloseWorker in .NET does not start either).
if not checkbox_client(settings.checkbox).is_fake and settings.checkbox.auto_close_interval_minutes > 0:
    beat_schedule["cashbox-auto-close"] = {
        "task": "cashbox.auto_close",
        "schedule": settings.checkbox.auto_close_interval_minutes * 60,
    }
if settings.pre_orders.settle_interval_minutes > 0:
    beat_schedule["preorders-settle"] = {
        "task": "preorders.settle",
        "schedule": settings.pre_orders.settle_interval_minutes * 60,
    }
if (check_at := daily_at(settings.competitors.check_time)) is not None:
    beat_schedule["competitors-check"] = {"task": "competitors.check", "schedule": check_at}
app.conf.beat_schedule = beat_schedule
