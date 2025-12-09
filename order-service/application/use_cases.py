from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, List

from domain.order import ItemLine, Order, ValidationError


class OrderRepository(Protocol):
    async def get(self, order_id: str) -> Order | None: ...
    async def add(self, order: Order) -> None: ...


class OutboxWriter(Protocol):
    async def put(self, event_type: str, payload: dict) -> None: ...


class UnitOfWork(Protocol):
    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    async def commit(self) -> None: ...
    @property
    def orders(self) -> OrderRepository: ...
    @property
    def outbox(self) -> OutboxWriter: ...


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
            return order
