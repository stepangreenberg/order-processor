import os
import json
import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer

from infrastructure import db
from infrastructure.message_bus import OrderCreatedConsumer

postgres_container = None


@pytest.fixture(scope="module", autouse=True)
def setup_test_database():
    """Setup PostgreSQL container for tests."""
    global postgres_container
    postgres_container = PostgresContainer("postgres:15-alpine")
    postgres_container.start()
    dsn = postgres_container.get_connection_url(driver="asyncpg")
    os.environ["APP__DB_DSN"] = dsn

    yield

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
async def test_consumer_processes_order_created(initialized_db):
    """Test that consumer processes order.created event and publishes result to outbox."""
    engine = initialized_db

    # Create consumer
    consumer = OrderCreatedConsumer(engine)

    # Order.created event
    message_payload = {
        "order_id": "ord-proc-001",
        "items": ["item1", "item2"],
        "amount": 150.0,
        "version": 1
    }

    # Handle the message
    await consumer.handle_message(json.dumps(message_payload))

    # Verify processing state was created
    uow = db.SqlAlchemyUnitOfWork(engine)
    async with uow:
        state = await uow.processing_states.get("ord-proc-001")
        assert state is not None
        assert state.order_id == "ord-proc-001"
        assert state.version == 1
        assert state.status in ["success", "failed"]  # Random outcome
        assert state.attempt_count == 1

        # Verify event published to outbox
        result = await uow.session.execute(
            db.outbox.select().where(db.outbox.c.event_type == "order.processed")
        )
        outbox_events = result.fetchall()
        assert len(outbox_events) == 1

        event = outbox_events[0]._mapping
        payload = event["payload"]
        assert payload["order_id"] == "ord-proc-001"
        assert payload["status"] in ["success", "failed"]
        assert payload["version"] == 1
