"""RabbitMQ message bus and consumers for processor service."""

import json
import os
from datetime import datetime, timezone
from typing import Optional

import aio_pika
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from infrastructure.db import SqlAlchemyUnitOfWork, outbox
from application.use_cases import HandleOrderCreatedUseCase, HandleOrderCreatedCommand


class OrderCreatedConsumer:
    """Consumer for order.created events from RabbitMQ."""

    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def handle_message(self, message_body: str) -> None:
        """
        Handle incoming order.created message.
        Processes the order and publishes result to outbox.
        Uses inbox pattern for deduplication.
        """
        # Parse message
        payload = json.loads(message_body)
        order_id = payload["order_id"]
        items = payload.get("items", [])
        amount = payload["amount"]
        version = payload.get("version", 1)

        # Create command
        command = HandleOrderCreatedCommand(
            order_id=order_id,
            items=items,
            amount=amount,
            version=version
        )

        # Execute use case (handles inbox, processing, and outbox internally)
        uow = SqlAlchemyUnitOfWork(self.engine)
        use_case = HandleOrderCreatedUseCase(uow)
        await use_case.execute(command)


class OutboxPublisher:
    """Publishes pending events from outbox to RabbitMQ."""

    def __init__(self, engine: AsyncEngine, rabbitmq_url: Optional[str] = None):
        self.engine = engine
        self.rabbitmq_url = rabbitmq_url or os.getenv("APP__RABBITMQ_URL", "amqp://guest:guest@localhost/")

    async def publish_pending(self) -> int:
        """
        Publish all pending (unpublished) events from outbox to RabbitMQ.
        Returns number of events successfully published.
        On failure, increments retry_count for failed events.
        """
        # Get unpublished events
        async with self.engine.begin() as conn:
            result = await conn.execute(
                select(outbox).where(outbox.c.published_at.is_(None))
            )
            events = result.fetchall()

        if not events:
            return 0

        published_count = 0

        # Try to publish each event independently
        for event in events:
            event_data = event._mapping

            try:
                # Connect to RabbitMQ
                connection = await aio_pika.connect_robust(self.rabbitmq_url)
                async with connection:
                    channel = await connection.channel()

                    # Publish to exchange
                    exchange = await channel.declare_exchange(
                        "orders",
                        aio_pika.ExchangeType.TOPIC,
                        durable=True
                    )

                    # Convert payload to JSON string
                    message_body = json.dumps(event_data["payload"]).encode()

                    # Publish message
                    await exchange.publish(
                        aio_pika.Message(
                            body=message_body,
                            content_type="application/json",
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                        ),
                        routing_key=event_data["event_type"]
                    )

                # Mark as published (only if publish succeeded)
                async with self.engine.begin() as conn:
                    await conn.execute(
                        update(outbox)
                        .where(outbox.c.id == event_data["id"])
                        .values(published_at=datetime.now(timezone.utc).isoformat())
                    )

                published_count += 1

            except Exception as e:
                # On failure, increment retry_count
                async with self.engine.begin() as conn:
                    await conn.execute(
                        update(outbox)
                        .where(outbox.c.id == event_data["id"])
                        .values(retry_count=outbox.c.retry_count + 1)
                    )
                # Continue to next event (don't fail entire batch)

        return published_count
