import asyncio
from dataclasses import dataclass, field
from typing import Dict, List

import pytest

from application.use_cases import CreateOrderCommand, CreateOrderUseCase
from domain.order import ItemLine, Order


class InMemoryOrderRepo:
    def __init__(self):
        self.storage: Dict[str, Order] = {}

    async def get(self, order_id: str) -> Order | None:
        return self.storage.get(order_id)

    async def add(self, order: Order) -> None:
        self.storage[order.order_id] = order


class InMemoryOutbox:
    def __init__(self):
        self.events: List[dict] = []

    async def put(self, event_type: str, payload: dict) -> None:
        self.events.append({"event_type": event_type, "payload": payload})


@dataclass
class InMemoryUoW:
    orders: InMemoryOrderRepo = field(default_factory=InMemoryOrderRepo)
    outbox: InMemoryOutbox = field(default_factory=InMemoryOutbox)
    committed: bool = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc:
            self.committed = False
        return False

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_create_order_saves_and_emits_outbox():
    uow = InMemoryUoW()
    use_case = CreateOrderUseCase(uow)
    cmd = CreateOrderCommand(
        order_id="ord-1",
        customer_id="cust-1",
        items=[ItemLine(sku="sku-1", quantity=2, price=5.0)],
    )

    order = await use_case.execute(cmd)

    assert order.order_id == "ord-1"
    assert uow.orders.storage["ord-1"].total_amount == 10.0
    assert uow.committed is True
    assert len(uow.outbox.events) == 1
    event = uow.outbox.events[0]
    assert event["event_type"] == "order.created"
    assert event["payload"]["order_id"] == "ord-1"
    assert event["payload"]["version"] == 1


@pytest.mark.asyncio
async def test_create_order_is_idempotent_on_existing():
    uow = InMemoryUoW()
    existing = Order.create(
        order_id="ord-1",
        customer_id="cust-1",
        items=[ItemLine(sku="sku-1", quantity=1, price=1.0)],
    )
    await uow.orders.add(existing)

    use_case = CreateOrderUseCase(uow)
    cmd = CreateOrderCommand(
        order_id="ord-1",
        customer_id="cust-1",
        items=[ItemLine(sku="sku-1", quantity=2, price=5.0)],
    )

    order = await use_case.execute(cmd)

    assert order is existing
    assert len(uow.outbox.events) == 0
    assert uow.committed is False
