"""Tests for Dead Letter Queue (DLQ) functionality."""
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from infrastructure import db
from infrastructure.message_bus import OutboxPublisher


@pytest.fixture(scope="module")
def postgres_container():
    """Start PostgreSQL container once for all tests."""
    with PostgresContainer("postgres:15-alpine") as postgres:
        yield postgres


@pytest_asyncio.fixture
async def db_engine(postgres_container):
    """Create a fresh database for each test."""
    dsn = postgres_container.get_connection_url(driver="asyncpg")
    engine = create_async_engine(dsn, future=True, poolclass=NullPool)

    # Drop and recreate tables to ensure clean state between tests
    async with engine.begin() as conn:
        await conn.run_sync(db.metadata.drop_all)
        await conn.run_sync(db.metadata.create_all)

    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_event_moved_to_dlq_after_max_retries(db_engine):
    """Test that events are moved to DLQ after reaching MAX_RETRIES."""
    # Create event that has reached MAX_RETRIES
    async with db_engine.begin() as conn:
        result = await conn.execute(
            db.outbox.insert().values(
                event_type="order.failed",
                payload={"order_id": "ord-dlq-123", "reason": "processing_failed"},
                published_at=None,
                retry_count=5,  # MAX_RETRIES
                last_retry_at=datetime.now(timezone.utc).isoformat()
            ).returning(db.outbox.c.id)
        )
        outbox_id = result.scalar()

    # Publisher should move this to DLQ
    publisher = OutboxPublisher(db_engine, rabbitmq_url="amqp://invalid")
    await publisher.publish_pending()

    # Event should be removed from outbox
    async with db_engine.begin() as conn:
        result = await conn.execute(
            select(db.outbox).where(db.outbox.c.id == outbox_id)
        )
        assert result.first() is None  # Event removed from outbox

    # Event should be in DLQ
    async with db_engine.begin() as conn:
        result = await conn.execute(
            select(db.dead_letter_queue)
            .where(db.dead_letter_queue.c.original_event_type == "order.failed")
        )
        dlq_event = result.first()

    assert dlq_event is not None
    assert dlq_event.original_event_type == "order.failed"
    assert dlq_event.payload == {"order_id": "ord-dlq-123", "reason": "processing_failed"}
    assert dlq_event.retry_count == 5
    assert dlq_event.failure_reason is not None  # Should have a reason
    assert dlq_event.moved_to_dlq_at is not None  # Should have timestamp


@pytest.mark.asyncio
async def test_dlq_table_structure(db_engine):
    """Test that DLQ table has correct columns."""
    # Insert a test event into DLQ directly
    async with db_engine.begin() as conn:
        await conn.execute(
            db.dead_letter_queue.insert().values(
                original_event_type="test.event",
                payload={"test": "data"},
                retry_count=5,
                last_retry_at=datetime.now(timezone.utc).isoformat(),
                failure_reason="Max retries exceeded",
                moved_to_dlq_at=datetime.now(timezone.utc).isoformat()
            )
        )

    # Verify we can read it back
    async with db_engine.begin() as conn:
        result = await conn.execute(
            select(db.dead_letter_queue)
        )
        event = result.first()

    assert event is not None
    assert event.id is not None
    assert event.original_event_type == "test.event"
    assert event.payload == {"test": "data"}
    assert event.retry_count == 5
    assert event.last_retry_at is not None
    assert event.failure_reason == "Max retries exceeded"
    assert event.moved_to_dlq_at is not None


@pytest.mark.asyncio
async def test_events_below_max_retries_not_moved_to_dlq(db_engine):
    """Test that events below MAX_RETRIES are not moved to DLQ."""
    # Create event with retry_count < MAX_RETRIES
    async with db_engine.begin() as conn:
        result = await conn.execute(
            db.outbox.insert().values(
                event_type="order.normal",
                payload={"order_id": "ord-normal-123"},
                published_at=None,
                retry_count=3,  # Below MAX_RETRIES
                last_retry_at=(datetime.now(timezone.utc)).isoformat()
            ).returning(db.outbox.c.id)
        )
        outbox_id = result.scalar()

    # Publisher should skip this (due to backoff, but not move to DLQ)
    publisher = OutboxPublisher(db_engine, rabbitmq_url="amqp://invalid")
    await publisher.publish_pending()

    # Event should still be in outbox
    async with db_engine.begin() as conn:
        result = await conn.execute(
            select(db.outbox).where(db.outbox.c.id == outbox_id)
        )
        assert result.first() is not None  # Event still in outbox

    # DLQ should be empty
    async with db_engine.begin() as conn:
        result = await conn.execute(select(db.dead_letter_queue))
        assert result.first() is None  # No events in DLQ
