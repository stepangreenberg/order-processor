"""Tests for Alembic database migrations."""
import pytest
import pytest_asyncio
from sqlalchemy import text, inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer
from alembic import command
from alembic.config import Config
import os


@pytest.fixture(scope="module")
def postgres_container():
    """Start PostgreSQL container once for all tests."""
    with PostgresContainer("postgres:15-alpine") as postgres:
        yield postgres


@pytest_asyncio.fixture
async def migration_engine(postgres_container):
    """Create a fresh database engine for migration testing."""
    dsn = postgres_container.get_connection_url(driver="asyncpg")
    engine = create_async_engine(dsn, future=True, poolclass=NullPool)

    yield engine, dsn

    await engine.dispose()


@pytest.mark.asyncio
async def test_migration_upgrade_creates_all_tables(migration_engine):
    """Test that migration upgrade creates all expected tables."""
    engine, dsn = migration_engine

    # Set environment variable for Alembic
    os.environ["APP__DB_DSN"] = dsn

    # Create Alembic config
    alembic_cfg = Config("alembic.ini")

    # Run migration
    command.upgrade(alembic_cfg, "head")

    # Verify tables were created
    async with engine.begin() as conn:
        inspector = await conn.run_sync(lambda sync_conn: inspect(sync_conn))
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())

    expected_tables = {'orders', 'outbox', 'processed_inbox', 'dead_letter_queue', 'alembic_version'}
    assert set(tables) == expected_tables


@pytest.mark.asyncio
async def test_migration_downgrade_removes_all_tables(migration_engine):
    """Test that migration downgrade removes all tables."""
    engine, dsn = migration_engine

    # Set environment variable for Alembic
    os.environ["APP__DB_DSN"] = dsn

    # Create Alembic config
    alembic_cfg = Config("alembic.ini")

    # Run upgrade first
    command.upgrade(alembic_cfg, "head")

    # Verify tables exist
    async with engine.begin() as conn:
        tables_before = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    assert len(tables_before) >= 4

    # Run downgrade
    command.downgrade(alembic_cfg, "base")

    # Verify only alembic_version remains
    async with engine.begin() as conn:
        tables_after = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())

    assert set(tables_after) == {'alembic_version'}


@pytest.mark.asyncio
async def test_migration_creates_correct_schema(migration_engine):
    """Test that migration creates tables with correct columns."""
    engine, dsn = migration_engine

    # Set environment variable for Alembic
    os.environ["APP__DB_DSN"] = dsn

    # Create Alembic config
    alembic_cfg = Config("alembic.ini")

    # Run migration
    command.upgrade(alembic_cfg, "head")

    # Verify orders table schema
    async with engine.begin() as conn:
        orders_cols = await conn.run_sync(
            lambda sync_conn: [col['name'] for col in inspect(sync_conn).get_columns('orders')]
        )

    assert 'order_id' in orders_cols
    assert 'customer_id' in orders_cols
    assert 'items' in orders_cols
    assert 'amount' in orders_cols
    assert 'status' in orders_cols
    assert 'version' in orders_cols

    # Verify outbox table schema
    async with engine.begin() as conn:
        outbox_cols = await conn.run_sync(
            lambda sync_conn: [col['name'] for col in inspect(sync_conn).get_columns('outbox')]
        )

    assert 'id' in outbox_cols
    assert 'event_type' in outbox_cols
    assert 'payload' in outbox_cols
    assert 'published_at' in outbox_cols
    assert 'retry_count' in outbox_cols
    assert 'last_retry_at' in outbox_cols

    # Verify dead_letter_queue table schema
    async with engine.begin() as conn:
        dlq_cols = await conn.run_sync(
            lambda sync_conn: [col['name'] for col in inspect(sync_conn).get_columns('dead_letter_queue')]
        )

    assert 'id' in dlq_cols
    assert 'original_event_type' in dlq_cols
    assert 'payload' in dlq_cols
    assert 'retry_count' in dlq_cols
    assert 'last_retry_at' in dlq_cols
    assert 'failure_reason' in dlq_cols
    assert 'moved_to_dlq_at' in dlq_cols


@pytest.mark.asyncio
async def test_migration_idempotent(migration_engine):
    """Test that running migration twice doesn't cause errors."""
    engine, dsn = migration_engine

    # Set environment variable for Alembic
    os.environ["APP__DB_DSN"] = dsn

    # Create Alembic config
    alembic_cfg = Config("alembic.ini")

    # Run migration twice
    command.upgrade(alembic_cfg, "head")
    command.upgrade(alembic_cfg, "head")  # Should be no-op

    # Verify tables still exist
    async with engine.begin() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())

    expected_tables = {'orders', 'outbox', 'processed_inbox', 'dead_letter_queue', 'alembic_version'}
    assert set(tables) == expected_tables
