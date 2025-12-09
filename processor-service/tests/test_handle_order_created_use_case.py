import pytest

from application.use_cases import HandleOrderCreatedCommand, HandleOrderCreatedUseCase
from domain.processing import ProcessingState


class InMemoryInbox:
    def __init__(self):
        self.keys = set()

    async def exists(self, event_key: str) -> bool:
        return event_key in self.keys

    async def add(self, event_key: str) -> None:
        self.keys.add(event_key)


class InMemoryStateRepo:
    def __init__(self):
        self.storage = {}

    async def get(self, order_id: str):
        return self.storage.get(order_id)

    async def upsert(self, state: ProcessingState):
        self.storage[state.order_id] = state


class InMemoryOutbox:
    def __init__(self):
        self.events = []

    async def put(self, event_type: str, payload: dict):
        self.events.append({"event_type": event_type, "payload": payload})


class InMemoryUoW:
    def __init__(self):
        self.states = InMemoryStateRepo()
        self.outbox = InMemoryOutbox()
        self.inbox = InMemoryInbox()
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_handle_order_created_publishes_processed():
    uow = InMemoryUoW()
    use_case = HandleOrderCreatedUseCase(uow)
    cmd = HandleOrderCreatedCommand(
        order_id="ord-1", items=["normal"], amount=10.0, version=1
    )

    result = await use_case.execute(cmd)

    assert result is not None
    assert result.status in ("success", "failed")
    assert uow.committed is True
    assert len(uow.outbox.events) == 1
    event = uow.outbox.events[0]
    assert event["event_type"] == "order.processed"
    assert event["payload"]["order_id"] == "ord-1"
    assert event["payload"]["version"] == 1


@pytest.mark.asyncio
async def test_handle_order_created_is_idempotent_with_inbox():
    uow = InMemoryUoW()
    use_case = HandleOrderCreatedUseCase(uow)
    cmd = HandleOrderCreatedCommand(
        order_id="ord-1", items=["normal"], amount=10.0, version=1
    )

    await use_case.execute(cmd)
    second = await use_case.execute(cmd)

    assert second is None
    assert len(uow.outbox.events) == 1  # no duplicate
    assert f"order.created:ord-1:1" in uow.inbox.keys


@pytest.mark.asyncio
async def test_handle_order_created_ignores_stale_version():
    uow = InMemoryUoW()
    existing = ProcessingState(order_id="ord-1", version=2, status="done")
    await uow.states.upsert(existing)
    use_case = HandleOrderCreatedUseCase(uow)
    cmd = HandleOrderCreatedCommand(
        order_id="ord-1", items=["normal"], amount=10.0, version=1
    )

    res = await use_case.execute(cmd)

    assert res is None
    assert len(uow.outbox.events) == 0
