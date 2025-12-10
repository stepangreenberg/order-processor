from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, insert, select
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from domain.processing import ProcessingState

metadata = MetaData()

# Processing state table - stores state of order processing
processing_states = Table(
    "processing_states",
    metadata,
    Column("order_id", String, primary_key=True),
    Column("version", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("last_error", String, nullable=True),
)

# Outbox table - for reliable event publishing
outbox = Table(
    "outbox",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event_type", String, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("published_at", String, nullable=True),
    Column("retry_count", Integer, nullable=False, default=0),
    Column("last_retry_at", String, nullable=True),
)

# Inbox table - for event deduplication
processed_inbox = Table(
    "processed_inbox",
    metadata,
    Column("event_key", String, primary_key=True),
)


def get_engine(dsn: Optional[str] = None) -> AsyncEngine:
    """Get database engine from DSN or environment variable."""
    url = dsn or os.getenv("APP__DB_DSN")
    if not url:
        raise RuntimeError("APP__DB_DSN not set")
    return create_async_engine(url, future=True)


class ProcessingStateRepository:
    """Repository for managing processing state."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, order_id: str) -> ProcessingState | None:
        """Get processing state by order ID."""
        result = await self.session.execute(
            select(processing_states).where(processing_states.c.order_id == order_id)
        )
        row = result.first()
        if not row:
            return None

        data = row._mapping
        return ProcessingState(
            order_id=data["order_id"],
            version=data["version"],
            status=data["status"],
            attempt_count=data["attempt_count"],
            last_error=data["last_error"],
        )

    async def add(self, state: ProcessingState) -> None:
        """Add or update processing state (upsert)."""
        stmt = pg_insert(processing_states).values(
            order_id=state.order_id,
            version=state.version,
            status=state.status,
            attempt_count=state.attempt_count,
            last_error=state.last_error,
        )
        # On conflict, update with new values
        stmt = stmt.on_conflict_do_update(
            index_elements=[processing_states.c.order_id],
            set_={
                "version": state.version,
                "status": state.status,
                "attempt_count": state.attempt_count,
                "last_error": state.last_error,
            }
        )
        await self.session.execute(stmt)

    async def upsert(self, state: ProcessingState) -> None:
        """Alias for add - both perform upsert."""
        await self.add(state)


class OutboxWriter:
    """Writer for outbox pattern - reliable event publishing."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def put(self, event_type: str, payload: dict) -> None:
        """Put event into outbox for later publishing."""
        stmt = insert(outbox).values(
            event_type=event_type,
            payload=payload,
            published_at=None,
            retry_count=0
        )
        await self.session.execute(stmt)


class InboxStore:
    """Store for inbox pattern - event deduplication."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def exists(self, event_key: str) -> bool:
        """Check if event key has been processed."""
        result = await self.session.execute(
            select(processed_inbox.c.event_key).where(processed_inbox.c.event_key == event_key)
        )
        return result.first() is not None

    async def add(self, event_key: str) -> None:
        """Add event key to inbox (idempotent)."""
        stmt = pg_insert(processed_inbox).values(event_key=event_key).on_conflict_do_nothing()
        await self.session.execute(stmt)


class SqlAlchemyUnitOfWork:
    """Unit of Work pattern for transaction management."""

    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.session_factory = async_sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session: AsyncSession | None = None
        self._processing_states_repo: ProcessingStateRepository | None = None
        self._outbox: OutboxWriter | None = None
        self._inbox: InboxStore | None = None

    async def __aenter__(self):
        self.session = self.session_factory()
        await self.session.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session:
            await self.session.__aexit__(exc_type, exc, tb)
        self.session = None

    async def commit(self) -> None:
        """Commit the current transaction."""
        if self.session:
            await self.session.commit()

    @property
    def processing_states(self) -> ProcessingStateRepository:
        """Get processing state repository."""
        if not self._processing_states_repo:
            if not self.session:
                raise RuntimeError("Session not initialized")
            self._processing_states_repo = ProcessingStateRepository(self.session)
        return self._processing_states_repo

    @property
    def states(self) -> ProcessingStateRepository:
        """Alias for processing_states (for Protocol compatibility)."""
        return self.processing_states

    @property
    def outbox(self) -> OutboxWriter:
        """Get outbox writer."""
        if not self._outbox:
            if not self.session:
                raise RuntimeError("Session not initialized")
            self._outbox = OutboxWriter(self.session)
        return self._outbox

    @property
    def inbox(self) -> InboxStore:
        """Get inbox store."""
        if not self._inbox:
            if not self.session:
                raise RuntimeError("Session not initialized")
            self._inbox = InboxStore(self.session)
        return self._inbox
