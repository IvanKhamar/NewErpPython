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
from newerp.application.customer_admin import CustomerAdminService
from newerp.application.delivery.services import DeliveryService
from newerp.application.stock.alerts import LowStockAlertOptions, LowStockAlertService
from newerp.config import Settings, get_settings
from newerp.infrastructure.cashbox.checkbox import checkbox_client
from newerp.infrastructure.cashbox.repository import SqlCashboxRepository
from newerp.infrastructure.catalog.pre_orders import SqlPreOrderStore
from newerp.infrastructure.competitors import HttpPageFetcher, SqlCompetitorStore
from newerp.infrastructure.customer_admin import SqlCustomerAdminStore
from newerp.infrastructure.delivery.nova_poshta import nova_poshta_client
from newerp.infrastructure.delivery.repositories import SqlShipmentRepository
from newerp.infrastructure.sms import sms_sender
from newerp.infrastructure.stock.repositories import SqlStockRepository

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


@app.task(name="delivery.refresh_tracking")
def delivery_refresh_tracking() -> dict:
    """Refreshes the statuses of open Nova Poshta waybills, like POST /delivery/tracking/refresh. The .NET version
    has only that button; here beat also runs it on a schedule."""

    async def work(session: AsyncSession) -> dict:
        client = nova_poshta_client(settings.nova_poshta)
        service = DeliveryService(SqlShipmentRepository(session), client, SystemClock())
        return (await service.refresh_tracking()).model_dump(by_alias=True)

    return asyncio.run(with_session(work))


@app.task(name="stock.low_stock_alert")
def stock_low_stock_alert() -> dict:
    """An SMS about the consumables that fell below their minimum since yesterday's run (Python first)."""

    async def work(session: AsyncSession) -> dict:
        options = LowStockAlertOptions(tuple(settings.stock.alert_phones))
        service = LowStockAlertService(
            SqlStockRepository(session), sms_sender(settings.sms_club), options, SystemClock()
        )
        result = await service.send()
        if result.items:
            logger.info("Мало на складі: позицій %s, SMS надіслано %s з %s", result.items, result.sent, result.phones)
        return result.model_dump(by_alias=True)

    return asyncio.run(with_session(work))


def daily_at(setting: str, name: str = "competitors.check_time") -> crontab | None:
    """crontab for "HH:mm" (like a cron line "mm HH * * *"); "-" or a wrong value: no schedule."""
    text = setting.strip()
    if text == "-":
        return None
    try:
        at = datetime.strptime(text, "%H:%M")
    except ValueError:
        logger.error("%s «%s» — не час HH:mm; задачу вимкнено", name, text)
        return None
    return crontab(hour=at.hour, minute=at.minute)


def beat_schedule_for(config: Settings) -> dict[str, dict]:
    """The periodic tasks for these settings: a function, so tests can pass their own settings."""
    schedule: dict[str, dict] = {}
    # With the Checkbox stub there is nothing to close (CashboxAutoCloseWorker in .NET does not start either).
    if not checkbox_client(config.checkbox).is_fake and config.checkbox.auto_close_interval_minutes > 0:
        schedule["cashbox-auto-close"] = {
            "task": "cashbox.auto_close",
            "schedule": config.checkbox.auto_close_interval_minutes * 60,
        }
    if config.pre_orders.settle_interval_minutes > 0:
        schedule["preorders-settle"] = {
            "task": "preorders.settle",
            "schedule": config.pre_orders.settle_interval_minutes * 60,
        }
    if (check_at := daily_at(config.competitors.check_time)) is not None:
        schedule["competitors-check"] = {"task": "competitors.check", "schedule": check_at}
    # Without phones there is no one to tell.
    if config.stock.alert_phones and (alert_at := daily_at(config.stock.alert_time, "stock.alert_time")) is not None:
        schedule["stock-low-stock-alert"] = {"task": "stock.low_stock_alert", "schedule": alert_at}
    if config.customers.login_link_minutes > 0:
        schedule["customers-expire-login-links"] = {"task": "customers.expire_login_links", "schedule": 60}
    # Without a key the stub has no waybills to track.
    if config.nova_poshta.tracking_key is not None and config.nova_poshta.tracking_interval_minutes > 0:
        schedule["delivery-refresh-tracking"] = {
            "task": "delivery.refresh_tracking",
            "schedule": config.nova_poshta.tracking_interval_minutes * 60,
        }
    return schedule


app.conf.beat_schedule = beat_schedule_for(settings)


@app.task(name="customers.expire_login_links")
def customers_expire_login_links() -> int:
    """Removes "log in as the customer" tokens older than their few minutes, if the storefront has not used them."""

    async def work(session: AsyncSession) -> int:
        service = CustomerAdminService(
            SqlCustomerAdminStore(session, settings.language_id),
            SystemClock(),
            settings.customers.store_url,
            settings.customers.login_link_minutes,
        )
        return await service.expire_login_links()

    return asyncio.run(with_session(work))
