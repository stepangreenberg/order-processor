import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from domain.order import ItemLine, Order
from infrastructure import db


@pytest.fixture(scope="module")
def postgres_container():
    """Start PostgreSQL container once for all tests."""
    with PostgresContainer("postgres:15-alpine") as postgres:
        yield postgres


@pytest_asyncio.fixture
async def db_engine(postgres_container):
    """Create a fresh database for each test."""
    dsn = postgres_container.get_connection_url(driver="asyncpg")

    engine = create_async_engine(
        dsn,
        future=True,
        poolclass=NullPool,
    )

    # Create and populate schema
    async with engine.begin() as conn:
        # Use a unique schema per test to avoid isolation issues
        import uuid
        schema_name = f"test_{uuid.uuid4().hex[:8]}"
        await conn.execute(text(f"CREATE SCHEMA {schema_name}"))
        await conn.execute(text(f"SET search_path TO {schema_name}"))
        await conn.run_sync(db.metadata.create_all)

    # Recreate engine with the schema in connection args
    await engine.dispose()
    engine = create_async_engine(
        dsn,
        future=True,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema_name}},
    )

    yield engine

    # Cleanup
    await engine.dispose()


@pytest.mark.asyncio
async def test_order_repo_add_and_get(db_engine):
    uow = db.SqlAlchemyUnitOfWork(db_engine)
    order = Order.create(
        order_id="ord-1",
        customer_id="cust-1",
        items=[ItemLine(sku="sku", quantity=2, price=5.0)],
    )

    async with uow:
        await uow.orders.add(order)
        await uow.commit()

    async with uow:
        fetched = await uow.orders.get("ord-1")

    assert fetched is not None
    assert fetched.order_id == "ord-1"
    assert fetched.status == "pending"
    assert fetched.version == 1
    assert fetched.total_amount == 10.0


@pytest.mark.asyncio
async def test_inbox_store_deduplicates(db_engine):
    uow = db.SqlAlchemyUnitOfWork(db_engine)
    key = "order.processed:ord-1:1"

    async with uow:
        await uow.inbox.add(key)
        await uow.commit()

    async with uow:
        exists = await uow.inbox.exists(key)
        again = await uow.inbox.exists("other")

    assert exists is True
    assert again is False


@pytest.mark.asyncio
async def test_outbox_put_persists_event(db_engine):
    uow = db.SqlAlchemyUnitOfWork(db_engine)

    async with uow:
        await uow.outbox.put("order.created", {"order_id": "ord-1"})
        await uow.commit()

    async with db_engine.begin() as conn:
        result = await conn.execute(select(db.outbox.c.event_type))
        rows = result.fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "order.created"
