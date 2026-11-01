"""Composition root of a Kafka reader: prints every order event, to see what the API publishes.

Run: `uv run python -m newerp.consumer` (needs NEWERP_KAFKA__BOOTSTRAP_SERVERS, e.g. localhost:9092).
Readers in one consumer group share the topic's partitions; each group keeps its own offset (how far it has read),
so a second group, for example a future e-mail sender, gets every event again. Ctrl+C stops it.
"""

import asyncio
import json

from aiokafka import AIOKafkaConsumer

from newerp.config import get_settings

GROUP_ID = "newerp-order-log"


async def main() -> None:
    kafka = get_settings().kafka
    if not kafka.enabled:
        raise SystemExit("Set NEWERP_KAFKA__BOOTSTRAP_SERVERS (for example localhost:9092).")

    consumer = AIOKafkaConsumer(
        kafka.topic,
        bootstrap_servers=kafka.bootstrap_servers,
        group_id=GROUP_ID,
        auto_offset_reset="earliest",  # a new group starts from the first event, not only new ones
    )
    await consumer.start()
    try:
        # async for: waits for messages without blocking the event loop; the offset is committed automatically.
        async for message in consumer:
            event = json.loads(message.value or b"{}")
            print(f"partition {message.partition} offset {message.offset}: {event}", flush=True)
    finally:
        await consumer.stop()


if __name__ == "__main__":
    asyncio.run(main())
