import os
import json
import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer

from infrastructure import db
from infrastructure.message_bus import OrderProcessedConsumer
from domain.order import Order, ItemLine

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
async def test_consumer_applies_processed_event(initialized_db):
    """Test that consumer applies order.processed event and updates order status."""
    engine = initialized_db

    # Create a pending order
    order = Order(
        order_id="ord-proc-123",
        customer_id="cust-456",
        items=[ItemLine(sku="widget", quantity=2, price=10.0)]
    )

    # Save order to database
    uow = db.SqlAlchemyUnitOfWork(engine)
    async with uow:
        await uow.orders.add(order)
        await uow.commit()

    # Verify initial status is pending
    async with uow:
        saved_order = await uow.orders.get("ord-proc-123")
        assert saved_order.status == "pending"
        assert saved_order.version == 1

    # Create consumer and process order.processed message
    consumer = OrderProcessedConsumer(engine)

    message_payload = {
        "order_id": "ord-proc-123",
        "status": "success",  # Will be mapped to "done" by use case
        "reason": None,
        "version": 2
    }

    # Handle the message
    await consumer.handle_message(json.dumps(message_payload))

    # Verify order status updated to done (success case)
    # Create fresh UnitOfWork for reading
    uow_read = db.SqlAlchemyUnitOfWork(engine)
    async with uow_read:
        updated_order = await uow_read.orders.get("ord-proc-123")
        assert updated_order is not None
        assert updated_order.status == "done"
        assert updated_order.version == 2


@pytest.mark.asyncio
async def test_consumer_handles_duplicate_events(initialized_db):
    """Test that consumer is idempotent - processing same event twice doesn't cause issues."""
    engine = initialized_db

    # Create a pending order
    order = Order(
        order_id="ord-dup-456",
        customer_id="cust-789",
        items=[ItemLine(sku="gadget", quantity=1, price=50.0)]
    )

    # Save order to database
    uow = db.SqlAlchemyUnitOfWork(engine)
    async with uow:
        await uow.orders.add(order)
        await uow.commit()

    # Create consumer and process order.processed message twice
    consumer = OrderProcessedConsumer(engine)

    message_payload = {
        "order_id": "ord-dup-456",
        "status": "success",
        "reason": None,
        "version": 2
    }

    # First processing
    await consumer.handle_message(json.dumps(message_payload))

    # Verify order updated
    uow_read = db.SqlAlchemyUnitOfWork(engine)
    async with uow_read:
        first_update = await uow_read.orders.get("ord-dup-456")
        assert first_update.status == "done"
        assert first_update.version == 2

    # Second processing (duplicate)
    await consumer.handle_message(json.dumps(message_payload))

    # Verify order still has same state (idempotent)
    async with uow_read:
        second_update = await uow_read.orders.get("ord-dup-456")
        assert second_update.status == "done"
        assert second_update.version == 2  # Version shouldn't change


@pytest.mark.asyncio
async def test_consumer_ignores_stale_versions(initialized_db):
    """Test that consumer ignores events with version <= current order version."""
    engine = initialized_db

    # Create an order and update it to version 3
    order = Order(
        order_id="ord-stale-789",
        customer_id="cust-abc",
        items=[ItemLine(sku="widget", quantity=1, price=100.0)]
    )
    order.version = 3  # Already at version 3
    order.status = "done"  # Already processed

    # Save order to database
    uow = db.SqlAlchemyUnitOfWork(engine)
    async with uow:
        await uow.orders.add(order)
        await uow.commit()

    # Try to process a stale event (version 2, which is older than current version 3)
    consumer = OrderProcessedConsumer(engine)

    stale_message = {
        "order_id": "ord-stale-789",
        "status": "failed",  # Different status
        "reason": "Something went wrong",
        "version": 2  # Stale version (< 3)
    }

    # Process stale message
    await consumer.handle_message(json.dumps(stale_message))

    # Verify order state unchanged (stale event ignored)
    uow_read = db.SqlAlchemyUnitOfWork(engine)
    async with uow_read:
        unchanged_order = await uow_read.orders.get("ord-stale-789")
        assert unchanged_order.status == "done"  # Should still be "done", not "failed"
        assert unchanged_order.version == 3  # Should still be version 3
