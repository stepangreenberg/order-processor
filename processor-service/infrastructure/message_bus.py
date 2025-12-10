"""RabbitMQ message bus and consumers for processor service."""

import json
from sqlalchemy.ext.asyncio import AsyncEngine

from infrastructure.db import SqlAlchemyUnitOfWork
from application.use_cases import HandleOrderCreatedUseCase, HandleOrderCreatedCommand


class OrderCreatedConsumer:
    """Consumer for order.created events from RabbitMQ."""

    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def handle_message(self, message_body: str) -> None:
        """
        Handle incoming order.created message.
        Processes the order and publishes result to outbox.
        Uses inbox pattern for deduplication.
        """
        # Parse message
        payload = json.loads(message_body)
        order_id = payload["order_id"]
        items = payload.get("items", [])
        amount = payload["amount"]
        version = payload.get("version", 1)

        # Create command
        command = HandleOrderCreatedCommand(
            order_id=order_id,
            items=items,
            amount=amount,
            version=version
        )

        # Execute use case (handles inbox, processing, and outbox internally)
        uow = SqlAlchemyUnitOfWork(self.engine)
        use_case = HandleOrderCreatedUseCase(uow)
        await use_case.execute(command)
