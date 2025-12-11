import pytest

from application.use_cases import ApplyProcessedCommand, ApplyProcessedUseCase
from domain.order import ItemLine, Order


class InMemoryInbox:
    def __init__(self):
        self.keys = set()

    async def exists(self, event_key: str) -> bool:
        return event_key in self.keys

    async def add(self, event_key: str) -> None:
        self.keys.add(event_key)


class InMemoryOrderRepo:
    def __init__(self):
        self.storage = {}

    async def get(self, order_id: str):
        return self.storage.get(order_id)

    async def add(self, order: Order):
        self.storage[order.order_id] = order


class InMemoryUoW:
    def __init__(self):
        self.orders = InMemoryOrderRepo()
        self.inbox = InMemoryInbox()
        self.outbox = None
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_apply_processed_updates_order_and_inbox():
    uow = InMemoryUoW()
    order = Order.create(
        order_id="ord-1",
        customer_id="cust-1",
        items=[ItemLine(sku="sku", quantity=1, price=10)],
    )
    uow.orders.storage[order.order_id] = order
    use_case = ApplyProcessedUseCase(uow)
    cmd = ApplyProcessedCommand(order_id="ord-1", status="success", fail_reason=None, version=2)

    updated = await use_case.execute(cmd)

    assert updated is not None
    assert updated.status == "done"
    assert updated.version == 2
    assert uow.committed is True
    assert f"order.processed:ord-1:2" in uow.inbox.keys


@pytest.mark.asyncio
async def test_apply_processed_is_idempotent_for_duplicates():
    uow = InMemoryUoW()
    order = Order.create(
        order_id="ord-1",
        customer_id="cust-1",
        items=[ItemLine(sku="sku", quantity=1, price=10)],
    )
    uow.orders.storage[order.order_id] = order
    use_case = ApplyProcessedUseCase(uow)
    cmd = ApplyProcessedCommand(order_id="ord-1", status="failed", fail_reason="oops", version=2)

    await use_case.execute(cmd)
    second = await use_case.execute(cmd)

    assert second is None
    # status fixed after first apply
    assert uow.orders.storage["ord-1"].status == "failed"
    # inbox has the key
    assert f"order.processed:ord-1:2" in uow.inbox.keys


@pytest.mark.asyncio
async def test_apply_processed_ignores_old_version():
    uow = InMemoryUoW()
    order = Order.create(
        order_id="ord-1",
        customer_id="cust-1",
        items=[ItemLine(sku="sku", quantity=1, price=10)],
    )
    order.version = 3
    uow.orders.storage[order.order_id] = order
    use_case = ApplyProcessedUseCase(uow)
    cmd = ApplyProcessedCommand(order_id="ord-1", status="success", fail_reason=None, version=2)

    res = await use_case.execute(cmd)

    assert res is None
    assert uow.orders.storage["ord-1"].version == 3
