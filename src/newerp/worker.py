"""Composition root of the Celery worker and beat (the background workers of .NET).

Run: `uv run celery -A newerp.worker worker --beat --pool=solo` (--pool=solo: Windows has no fork).
Celery beat is the scheduler that sends the periodic tasks below, like a BackgroundService with a PeriodicTimer.
"""

import asyncio
from collections.abc import Awaitable, Callable

from celery import Celery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from newerp.application.cashbox.services import CashboxService
from newerp.application.catalog.pre_orders import PreOrderService
from newerp.application.common import SystemClock
from newerp.config import get_settings
from newerp.infrastructure.cashbox.checkbox import checkbox_client
from newerp.infrastructure.cashbox.repository import SqlCashboxRepository
from newerp.infrastructure.catalog.pre_orders import SqlPreOrderStore

settings = get_settings()

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
app.conf.beat_schedule = beat_schedule
