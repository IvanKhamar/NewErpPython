"""Order events for other systems: Kafka (aiokafka) or, without Kafka settings, only the log."""

import json
import logging
from typing import Any, Protocol
from uuid import uuid4

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from newerp.application.orders.models import OrderCreatedEvent
from newerp.config import KafkaSettings

log = logging.getLogger(__name__)

ORDER_CREATED = "OrderCreated"


def order_created_message(event: OrderCreatedEvent) -> dict[str, Any]:
    """The JSON readers get: camelCase like the API, the total as a number. eventId lets a reader skip a repeat."""
    return {
        "eventId": str(uuid4()),
        "type": ORDER_CREATED,
        "occurredAt": event.occurred_at.isoformat(),
        "orderId": event.order_id,
        "statusId": event.status_id,
        "managerId": event.manager_id,
        "orderSourceId": event.order_source_id,
        "total": float(event.total),
    }


class Producer(Protocol):
    """The part of AIOKafkaProducer used here, so tests can pass a fake one."""

    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> Any: ...


class KafkaOrderEvents:
    """
    Writes each event to one topic. The key is the order id: Kafka sends equal keys to the same partition, so the
    events of one order are read in the order they were written.
    """

    def __init__(self, producer: Producer, topic: str) -> None:
        self._producer = producer
        self._topic = topic

    async def order_created(self, event: OrderCreatedEvent) -> None:
        value = json.dumps(order_created_message(event)).encode()
        try:
            # send_and_wait returns when the broker confirmed the write (send() alone only queues it).
            await self._producer.send_and_wait(self._topic, value=value, key=str(event.order_id).encode())
        except KafkaError:
            # The order is already saved; a request must not fail because Kafka is down.
            log.exception("OrderCreated for order %s was not sent to Kafka", event.order_id)


class LogOrderEvents:
    """The stub without Kafka: like the other outside services, local development only writes to the log."""

    async def order_created(self, event: OrderCreatedEvent) -> None:
        log.info("OrderCreated (Kafka is off): %s", order_created_message(event))


def kafka_producer(settings: KafkaSettings) -> AIOKafkaProducer:
    """One producer for the whole process (like a singleton IProducer in .NET); start() it before use."""
    # acks="all": the write counts only when every in-sync replica has it.
    return AIOKafkaProducer(bootstrap_servers=settings.bootstrap_servers, acks="all")
