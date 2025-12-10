import os
import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer
from testcontainers.rabbitmq import RabbitMqContainer
from sqlalchemy import select, text
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
