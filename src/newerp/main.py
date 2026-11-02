"""Composition root of the API (like Program.cs): builds the FastAPI app and plugs in the routers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from newerp.api import (
    auth,
    cashbox,
    catalog,
    competitors,
    customers,
    delivery,
    health,
    locations,
    marketing,
    orders,
    partners,
    payments,
    pre_orders,
    production,
    remarketing,
    reports,
    returns,
    sms,
    staff,
    stock,
    users,
)
from newerp.api.errors import add_error_handlers
from newerp.api.security import add_security_middleware
from newerp.config import get_settings
from newerp.infrastructure.db import engine
from newerp.infrastructure.events import KafkaOrderEvents, LogOrderEvents, kafka_producer
from newerp.web import add_web


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    kafka = get_settings().kafka
    producer = kafka_producer(kafka) if kafka.enabled else None
    if producer is not None:
        await producer.start()  # connects to the broker; fails the start if Kafka is unreachable
        app.state.order_events = KafkaOrderEvents(producer, kafka.topic)
    try:
        yield  # the app serves requests here
    finally:
        if producer is not None:
            await producer.stop()  # sends what is still queued
            app.state.order_events = LogOrderEvents()
        await engine.dispose()


app = FastAPI(title="NewErp API", version="0.1.0", lifespan=lifespan)
# The default until the lifespan connects Kafka (tests do not run the lifespan, so they keep it).
app.state.order_events = LogOrderEvents()
add_error_handlers(app)
add_security_middleware(app)

for module in (health, auth, users, orders, staff, stock, locations, production):
    app.include_router(module.router)
app.include_router(delivery.shipment_router)
app.include_router(delivery.delivery_router)
app.include_router(cashbox.router)
app.include_router(cashbox.order_router)
app.include_router(payments.router)
app.include_router(payments.order_router)
app.include_router(sms.router)
app.include_router(sms.settings_router)
app.include_router(partners.router)
app.include_router(partners.feed_router)
app.include_router(catalog.products_router)
app.include_router(catalog.sort_router)
app.include_router(pre_orders.router)
app.include_router(marketing.router)
app.include_router(marketing.order_router)
app.include_router(returns.router)
app.include_router(customers.router)
app.include_router(reports.router)
app.include_router(reports.feed_router)
app.include_router(remarketing.router)
app.include_router(remarketing.order_router)
app.include_router(competitors.router)
add_web(app)  # the HTML pages at /app/
