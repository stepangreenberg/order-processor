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
