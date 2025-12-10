import os
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from testcontainers.postgres import PostgresContainer
from testcontainers.rabbitmq import RabbitMqContainer
from sqlalchemy import select, text, update
import asyncio

from infrastructure import db
from infrastructure.message_bus import OutboxPublisher

postgres_container = None
rabbitmq_container = None


@pytest.fixture(scope="module", autouse=True)
def setup_test_containers():
    """Setup PostgreSQL and RabbitMQ containers for tests."""
    global postgres_container, rabbitmq_container

    # Start PostgreSQL
    postgres_container = PostgresContainer("postgres:15-alpine")
    postgres_container.start()
    dsn = postgres_container.get_connection_url(driver="asyncpg")
    os.environ["APP__DB_DSN"] = dsn

    # Start RabbitMQ
    rabbitmq_container = RabbitMqContainer("rabbitmq:3-management-alpine")
    rabbitmq_container.start()

    # Build AMQP URL from connection params
    host = rabbitmq_container.get_container_host_ip()
    port = rabbitmq_container.get_exposed_port(5672)
    rabbitmq_url = f"amqp://guest:guest@{host}:{port}/"
    os.environ["APP__RABBITMQ_URL"] = rabbitmq_url

    yield

    # Cleanup
    rabbitmq_container.stop()
    postgres_container.stop()


@pytest_asyncio.fixture
async def initialized_db():
    """Initialize database with schema."""
    engine = db.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(db.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.mark.asyncio
async def test_outbox_publisher_publishes_pending_events(initialized_db):
    """Test that OutboxPublisher publishes pending events from outbox to RabbitMQ."""
    engine = initialized_db

    # Insert unpublished event into outbox
    async with engine.begin() as conn:
        await conn.execute(
            db.outbox.insert().values(
                event_type="order.created",
                payload={"order_id": "ord-123", "customer_id": "cust-456"},
                published_at=None,
                retry_count=0
            )
        )

    # Create publisher and publish once
    publisher = OutboxPublisher(engine)
    published_count = await publisher.publish_pending()

    # Should have published 1 event
    assert published_count == 1

    # Verify event is marked as published in database
    async with engine.begin() as conn:
        result = await conn.execute(
            select(db.outbox.c.published_at).where(db.outbox.c.event_type == "order.created")
        )
        row = result.first()
        assert row is not None
        assert row.published_at is not None  # Should have timestamp


@pytest.mark.asyncio
async def test_outbox_publisher_retries_on_failure(initialized_db):
    """Test that OutboxPublisher increments retry_count on failure and can retry successfully."""
    engine = initialized_db

    # Insert unpublished event into outbox
    async with engine.begin() as conn:
        result = await conn.execute(
            db.outbox.insert().values(
                event_type="order.retry",
                payload={"order_id": "ord-retry-123"},
                published_at=None,
                retry_count=0
            ).returning(db.outbox.c.id)
        )
        event_id = result.scalar()

    # Try to publish with invalid RabbitMQ URL (should fail)
    publisher_bad = OutboxPublisher(engine, rabbitmq_url="amqp://guest:guest@invalid-host:5672/")

    try:
        await publisher_bad.publish_pending()
    except Exception:
        pass  # Expected to fail

    # Verify event is NOT marked as published
    async with engine.begin() as conn:
        result = await conn.execute(
            select(db.outbox.c.published_at, db.outbox.c.retry_count)
            .where(db.outbox.c.id == event_id)
        )
        row = result.first()
        assert row is not None
        assert row.published_at is None  # Should still be unpublished
        assert row.retry_count == 1  # Should increment retry_count

    # Set last_retry_at to 10 seconds ago so backoff delay has elapsed
    async with engine.begin() as conn:
        await conn.execute(
            update(db.outbox)
            .where(db.outbox.c.id == event_id)
            .values(last_retry_at=(datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat())
        )

    # Now retry with valid URL (should succeed since backoff has elapsed)
    publisher_good = OutboxPublisher(engine)  # Uses valid URL from env
    published_count = await publisher_good.publish_pending()

    assert published_count == 1

    # Verify event is now marked as published
    async with engine.begin() as conn:
        result = await conn.execute(
            select(db.outbox.c.published_at).where(db.outbox.c.id == event_id)
        )
        row = result.first()
        assert row is not None
        assert row.published_at is not None  # Should be published now
