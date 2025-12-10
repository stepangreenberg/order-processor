from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, insert, select
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from domain.order import ItemLine, Order

metadata = MetaData()

orders = Table(
    "orders",
    metadata,
    Column("order_id", String, primary_key=True),
    Column("customer_id", String, nullable=False),
    Column("items", JSONB, nullable=False),
    Column("amount", Float, nullable=False),
    Column("status", String, nullable=False),
    Column("version", Integer, nullable=False),
)

outbox = Table(
    "outbox",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event_type", String, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("published_at", String, nullable=True),
    Column("retry_count", Integer, nullable=False, default=0),
)

processed_inbox = Table(
    "processed_inbox",
    metadata,
    Column("event_key", String, primary_key=True),
)


def get_engine(dsn: Optional[str] = None) -> AsyncEngine:
    url = dsn or os.getenv("APP__DB_DSN")
    if not url:
        raise RuntimeError("APP__DB_DSN not set")
    return create_async_engine(url, future=True)


class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, order_id: str) -> Order | None:
        result = await self.session.execute(select(orders).where(orders.c.order_id == order_id))
        row = result.first()
        if not row:
            return None
        data = row._mapping
        return Order.hydrate(
            order_id=data["order_id"],
            customer_id=data["customer_id"],
            items=[ItemLine(**item) for item in data["items"]],
            status=data["status"],
            version=data["version"],
            total_amount=data["amount"],
        )

    async def add(self, order: Order) -> None:
        stmt = pg_insert(orders).values(
            order_id=order.order_id,
            customer_id=order.customer_id,
            items=[{"sku": i.sku, "quantity": i.quantity, "price": i.price} for i in order.items],
            amount=order.total_amount,
            status=order.status,
            version=order.version,
        )
        # Upsert: insert if new, update if exists
        stmt = stmt.on_conflict_do_update(
            index_elements=[orders.c.order_id],
            set_={
                "status": order.status,
                "version": order.version,
                "amount": order.total_amount,
                "items": [{"sku": i.sku, "quantity": i.quantity, "price": i.price} for i in order.items],
            }
        )
        await self.session.execute(stmt)


class OutboxWriter:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def put(self, event_type: str, payload: dict) -> None:
        stmt = insert(outbox).values(event_type=event_type, payload=payload, published_at=None, retry_count=0)
        await self.session.execute(stmt)


class InboxStore:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def exists(self, event_key: str) -> bool:
        result = await self.session.execute(select(processed_inbox.c.event_key).where(processed_inbox.c.event_key == event_key))
        return result.first() is not None

    async def add(self, event_key: str) -> None:
        stmt = pg_insert(processed_inbox).values(event_key=event_key).on_conflict_do_nothing()
        await self.session.execute(stmt)


class SqlAlchemyUnitOfWork:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.session_factory = async_sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session: AsyncSession | None = None
        self._orders_repo: OrderRepository | None = None
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
        if self.session:
            await self.session.commit()

    @property
    def orders(self) -> OrderRepository:
        if not self._orders_repo:
            if not self.session:
                raise RuntimeError("Session not initialized")
            self._orders_repo = OrderRepository(self.session)
        return self._orders_repo

    @property
    def outbox(self) -> OutboxWriter:
        if not self._outbox:
            if not self.session:
                raise RuntimeError("Session not initialized")
            self._outbox = OutboxWriter(self.session)
        return self._outbox

    @property
    def inbox(self) -> InboxStore:
        if not self._inbox:
            if not self.session:
                raise RuntimeError("Session not initialized")
            self._inbox = InboxStore(self.session)
        return self._inbox
