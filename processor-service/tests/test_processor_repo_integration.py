import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from domain.processing import ProcessingState
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
async def test_processing_state_repo_add_and_get(db_engine):
    """Test that we can save and retrieve ProcessingState from the database."""
    uow = db.SqlAlchemyUnitOfWork(db_engine)

    # Create initial processing state
    state = ProcessingState(
        order_id="ord-123",
        version=1,
        status="pending",
        attempt_count=0,
        last_error=None
    )

    # Save state
    async with uow:
        await uow.processing_states.add(state)
        await uow.commit()

    # Retrieve state
    async with uow:
        fetched = await uow.processing_states.get("ord-123")

    # Assertions
    assert fetched is not None
    assert fetched.order_id == "ord-123"
    assert fetched.version == 1
    assert fetched.status == "pending"
    assert fetched.attempt_count == 0
    assert fetched.last_error is None


@pytest.mark.asyncio
async def test_inbox_store_deduplicates(db_engine):
    """Test that inbox prevents duplicate event processing."""
    uow = db.SqlAlchemyUnitOfWork(db_engine)
    key = "order.created:ord-123:1"

    # Add event key to inbox
    async with uow:
        await uow.inbox.add(key)
        await uow.commit()

    # Check if event key exists
    async with uow:
        exists = await uow.inbox.exists(key)
        not_exists = await uow.inbox.exists("other-key")

    assert exists is True
    assert not_exists is False


@pytest.mark.asyncio
async def test_outbox_put_persists_event(db_engine):
    """Test that outbox stores events for publishing."""
    uow = db.SqlAlchemyUnitOfWork(db_engine)

    # Put event in outbox
    async with uow:
        await uow.outbox.put("order.processed", {
            "order_id": "ord-123",
            "status": "success",
            "version": 1
        })
        await uow.commit()

    # Verify event was persisted
    async with db_engine.begin() as conn:
        result = await conn.execute(select(db.outbox.c.event_type))
        rows = result.fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "order.processed"
