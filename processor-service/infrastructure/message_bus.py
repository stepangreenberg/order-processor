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


# Retry configuration
MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 300  # 5 minutes


def calculate_backoff_delay(retry_count: int) -> int:
    """Calculate exponential backoff delay in seconds."""
    if retry_count <= 0:
        return INITIAL_BACKOFF_SECONDS
    delay = INITIAL_BACKOFF_SECONDS * (2 ** (retry_count - 1))
    return min(delay, MAX_BACKOFF_SECONDS)


def should_retry_event(retry_count: int) -> bool:
    """Check if event should be retried based on retry count."""
    return retry_count < MAX_RETRIES


def should_retry_event_with_backoff(retry_count: int, last_retry_at: Optional[str]) -> bool:
    """Check if event should be retried considering backoff delay."""
    if not should_retry_event(retry_count):
        return False

    if last_retry_at is None:
        return True

    backoff_delay = calculate_backoff_delay(retry_count)
    last_retry = datetime.fromisoformat(last_retry_at)
    now = datetime.now(timezone.utc)

    # Handle timezone-naive datetime
    if last_retry.tzinfo is None:
        now = now.replace(tzinfo=None)

    time_since_retry = (now - last_retry).total_seconds()
    return time_since_retry >= backoff_delay


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

            # Check if event should be retried (respecting max retries and backoff)
            if not should_retry_event_with_backoff(
                event_data["retry_count"],
                event_data.get("last_retry_at")
            ):
                continue  # Skip this event (either max retries reached or backoff not elapsed)

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
                # On failure, increment retry_count and update last_retry_at
                async with self.engine.begin() as conn:
                    await conn.execute(
                        update(outbox)
                        .where(outbox.c.id == event_data["id"])
                        .values(
                            retry_count=outbox.c.retry_count + 1,
                            last_retry_at=datetime.now(timezone.utc).isoformat()
                        )
                    )
                # Continue to next event (don't fail entire batch)

        return published_count
