"""Tests for retry logic with exponential backoff and max retries."""
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from infrastructure import db
from infrastructure.message_bus import OutboxPublisher, should_retry_event


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
async def test_exponential_backoff_calculation():
    """Test that exponential backoff is calculated correctly."""
    from infrastructure.message_bus import calculate_backoff_delay

    # First retry: 5 seconds
    assert calculate_backoff_delay(1) == 5

    # Second retry: 10 seconds (5 * 2^1)
    assert calculate_backoff_delay(2) == 10

    # Third retry: 20 seconds (5 * 2^2)
    assert calculate_backoff_delay(3) == 20

    # Fourth retry: 40 seconds (5 * 2^3)
    assert calculate_backoff_delay(4) == 40

    # Max backoff: 300 seconds (5 minutes)
    assert calculate_backoff_delay(10) == 300


@pytest.mark.asyncio
async def test_should_retry_event_respects_max_retries():
    """Test that events are not retried after MAX_RETRIES."""
    # MAX_RETRIES = 5

    # Should retry if retry_count < MAX_RETRIES
    assert should_retry_event(0) is True
    assert should_retry_event(3) is True
    assert should_retry_event(4) is True

    # Should NOT retry if retry_count >= MAX_RETRIES
    assert should_retry_event(5) is False
    assert should_retry_event(6) is False
    assert should_retry_event(10) is False


@pytest.mark.asyncio
async def test_should_retry_event_checks_last_retry_time():
    """Test that events are only retried after backoff delay."""
    from infrastructure.message_bus import should_retry_event_with_backoff

    now = datetime.now(timezone.utc)

    # Event retried 1 second ago, backoff is 5 seconds -> should NOT retry yet
    last_retry = (now - timedelta(seconds=1)).isoformat()
    assert should_retry_event_with_backoff(1, last_retry) is False

    # Event retried 6 seconds ago, backoff is 5 seconds -> should retry
    last_retry = (now - timedelta(seconds=6)).isoformat()
    assert should_retry_event_with_backoff(1, last_retry) is True

    # Event retried 15 seconds ago, backoff is 10 seconds (retry_count=2) -> should retry
    last_retry = (now - timedelta(seconds=15)).isoformat()
    assert should_retry_event_with_backoff(2, last_retry) is True


@pytest.mark.asyncio
async def test_outbox_publisher_stops_at_max_retries(db_engine):
    """Test that OutboxPublisher moves events to DLQ after MAX_RETRIES."""
    # Create event that has reached MAX_RETRIES
    async with db_engine.begin() as conn:
        await conn.execute(
            db.outbox.insert().values(
                event_type="test.event",
                payload={"test": "data"},
                published_at=None,
                retry_count=5,  # MAX_RETRIES
                last_retry_at=datetime.now(timezone.utc).isoformat()
            )
        )

    # Publisher should move this event to DLQ
    publisher = OutboxPublisher(db_engine, rabbitmq_url="amqp://invalid")

    # This should not raise an exception, just move event to DLQ
    result = await publisher.publish_pending()

    # Event should be removed from outbox
    async with db_engine.begin() as conn:
        rows = await conn.execute(select(db.outbox))
        assert rows.first() is None  # Event removed from outbox

    # Event should be in DLQ
    async with db_engine.begin() as conn:
        rows = await conn.execute(select(db.dead_letter_queue))
        dlq_event = rows.first()

    assert dlq_event is not None
    assert dlq_event.original_event_type == "test.event"
    assert dlq_event.retry_count == 5
    assert dlq_event.failure_reason == "Max retries (5) exceeded"


@pytest.mark.asyncio
async def test_outbox_publisher_respects_backoff_delay(db_engine):
    """Test that OutboxPublisher respects exponential backoff delay."""
    # Create event that was just retried (backoff is 5 seconds for retry_count=1)
    # Set last_retry_at to NOW to guarantee backoff hasn't elapsed
    async with db_engine.begin() as conn:
        await conn.execute(
            db.outbox.insert().values(
                event_type="test.event",
                payload={"test": "data"},
                published_at=None,
                retry_count=1,
                last_retry_at=datetime.now(timezone.utc).isoformat()
            )
        )

    publisher = OutboxPublisher(db_engine, rabbitmq_url="amqp://invalid")

    # Should skip this event because backoff delay not elapsed (just retried)
    result = await publisher.publish_pending()

    assert result == 0  # No events published

    # Event should still have retry_count=1 (not incremented)
    async with db_engine.begin() as conn:
        rows = await conn.execute(select(db.outbox))
        event = rows.first()

    assert event.retry_count == 1
