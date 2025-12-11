from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, List

from domain.order import ItemLine, Order, ValidationError
from infrastructure.metrics import metrics


class OrderRepository(Protocol):
    async def get(self, order_id: str) -> Order | None: ...
    async def add(self, order: Order) -> None: ...


class OutboxWriter(Protocol):
    async def put(self, event_type: str, payload: dict) -> None: ...


class InboxStore(Protocol):
    async def exists(self, event_key: str) -> bool: ...
    async def add(self, event_key: str) -> None: ...


class UnitOfWork(Protocol):
    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    async def commit(self) -> None: ...
    @property
    def orders(self) -> OrderRepository: ...
    @property
    def outbox(self) -> OutboxWriter: ...
    @property
    def inbox(self) -> InboxStore: ...


@dataclass
class CreateOrderCommand:
    order_id: str
    customer_id: str
    items: List[ItemLine]


class CreateOrderUseCase:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    async def execute(self, cmd: CreateOrderCommand) -> Order:
        async with self.uow:
            existing = await self.uow.orders.get(cmd.order_id)
            if existing:
                return existing

            order = Order.create(
                order_id=cmd.order_id,
                customer_id=cmd.customer_id,
                items=cmd.items,
            )
            # fail_reason None на старте (может быть заполнен обработчиком)
            order.fail_reason = None
            await self.uow.orders.add(order)
            await self.uow.outbox.put(
                event_type="order.created",
                payload={
                    "order_id": order.order_id,
                    "customer_id": order.customer_id,
                    "items": [{"sku": i.sku, "quantity": i.quantity, "price": i.price} for i in order.items],
                    "amount": order.total_amount,
                    "version": order.version,
                },
            )
            await self.uow.commit()
            metrics.increment("orders_created_total")
            return order


@dataclass
class ApplyProcessedCommand:
    order_id: str
    status: str
    fail_reason: str | None
    version: int


class ApplyProcessedUseCase:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    async def execute(self, cmd: ApplyProcessedCommand) -> Order | None:
        event_key = f"order.processed:{cmd.order_id}:{cmd.version}"
        async with self.uow:
            if await self.uow.inbox.exists(event_key):
                return None

            order = await self.uow.orders.get(cmd.order_id)
            if not order:
                return None
            if cmd.version <= order.version:
                return None

            order.status = "done" if cmd.status == "success" else "failed"
            order.version = cmd.version
            order.fail_reason = cmd.fail_reason
            await self.uow.inbox.add(event_key)
            await self.uow.orders.add(order)
            await self.uow.commit()
            metrics.increment("orders_processed_total")
            return order
