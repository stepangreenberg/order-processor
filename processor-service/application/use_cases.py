from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, List

from domain.processing import ProcessingResult, ProcessingState


class ProcessingStateRepo(Protocol):
    async def get(self, order_id: str) -> ProcessingState | None: ...
    async def upsert(self, state: ProcessingState) -> None: ...


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
    def states(self) -> ProcessingStateRepo: ...
    @property
    def outbox(self) -> OutboxWriter: ...
    @property
    def inbox(self) -> InboxStore: ...


@dataclass
class HandleOrderCreatedCommand:
    order_id: str
    items: List[str]
    amount: float
    version: int


class HandleOrderCreatedUseCase:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    async def execute(self, cmd: HandleOrderCreatedCommand) -> ProcessingResult | None:
        event_key = f"order.created:{cmd.order_id}:{cmd.version}"
        async with self.uow:
            if await self.uow.inbox.exists(event_key):
                return None

            state = await self.uow.states.get(cmd.order_id)
            if not state:
                state = ProcessingState(order_id=cmd.order_id, version=0)

            result = state.apply_order_created(
                items=cmd.items,
                amount=cmd.amount,
                version=cmd.version,
            )

            if result.status == "ignored":
                await self.uow.inbox.add(event_key)
                await self.uow.commit()
                return None

            await self.uow.states.upsert(state)
            await self.uow.inbox.add(event_key)
            await self.uow.outbox.put(
                event_type="order.processed",
                payload={
                    "order_id": cmd.order_id,
                    "status": result.status,
                    "reason": result.reason,
                    "version": state.version,
                },
            )
            await self.uow.commit()
            return result
