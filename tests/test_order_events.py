"""Order events: what the API publishes and what the Kafka adapter writes. No broker: fakes stand in for it."""

import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal

import pytest
from aiokafka.errors import KafkaConnectionError

from newerp.api.deps import get_order_events
from newerp.application.orders.models import OrderCreatedEvent
from newerp.infrastructure.events import KafkaOrderEvents
from newerp.main import app
from tests.seed import MANAGER, MANAGER_USER_ID, PENDING
from tests.test_new_orders import request


class FakeEvents:
    def __init__(self) -> None:
        self.created: list[OrderCreatedEvent] = []

    async def order_created(self, event: OrderCreatedEvent) -> None:
        self.created.append(event)


class FakeProducer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[tuple[str, bytes, bytes]] = []

    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append((topic, value, key))


@pytest.fixture
def events():
    fake = FakeEvents()
    app.dependency_overrides[get_order_events] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_order_events, None)


EVENT = OrderCreatedEvent(42, PENDING, MANAGER_USER_ID, 1, Decimal("1700.50"), datetime(2026, 11, 1, 12, 30))


def test_creating_an_order_publishes_order_created(api, events):
    response = api(MANAGER).post("/orders", json=request("+380501112277"))

    assert response.status_code == 201
    [event] = events.created
    assert (event.order_id, event.status_id, event.manager_id, event.order_source_id, event.total) == (
        response.json()["orderId"],
        PENDING,
        MANAGER_USER_ID,
        1,
        Decimal(1700),
    )


def test_an_invalid_order_publishes_nothing(api, events):
    response = api(MANAGER).post("/orders", json=request("+380501112288", quantity=0))

    assert response.status_code == 400
    assert events.created == []


def test_kafka_message_is_keyed_by_order_and_uses_camel_case():
    producer = FakeProducer()

    asyncio.run(KafkaOrderEvents(producer, "newerp.orders").order_created(EVENT))

    [(topic, value, key)] = producer.sent
    assert (topic, key) == ("newerp.orders", b"42")
    message = json.loads(value)
    assert message.pop("eventId")
    assert message == {
        "type": "OrderCreated",
        "occurredAt": "2026-11-01T12:30:00",
        "orderId": 42,
        "statusId": PENDING,
        "managerId": MANAGER_USER_ID,
        "orderSourceId": 1,
        "total": 1700.5,
    }


def test_kafka_failure_is_logged_not_raised(caplog):
    producer = FakeProducer(KafkaConnectionError("broker down"))

    with caplog.at_level(logging.ERROR):
        asyncio.run(KafkaOrderEvents(producer, "newerp.orders").order_created(EVENT))

    assert "order 42 was not sent" in caplog.text
