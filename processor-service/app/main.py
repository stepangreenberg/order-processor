import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from infrastructure import db
from infrastructure.message_bus import OutboxPublisher, OrderCreatedConsumer
import aio_pika


def get_service_name() -> str:
    return os.getenv("APP__SERVICE_NAME", "processor-service")


# Global engine (initialized at startup)
engine: AsyncEngine | None = None
# Background tasks
background_tasks: set[asyncio.Task] = set()


async def run_outbox_publisher():
    """Background task: periodically publish events from outbox to RabbitMQ."""
    if not engine:
        return

    publisher = OutboxPublisher(engine)
    while True:
        try:
            published = await publisher.publish_pending()
            if published > 0:
                print(f"Published {published} events from outbox")
        except Exception as e:
            print(f"Outbox publisher error: {e}")
        await asyncio.sleep(5)  # Poll every 5 seconds


async def run_rabbitmq_consumer():
    """Background task: consume order.created events from RabbitMQ."""
    if not engine:
        return

    rabbitmq_url = os.getenv("APP__RABBITMQ_URL", "amqp://guest:guest@localhost/")
    consumer = OrderCreatedConsumer(engine)

    while True:
        try:
            connection = await aio_pika.connect_robust(rabbitmq_url)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=10)

                # Declare exchange and queue
                exchange = await channel.declare_exchange(
                    "orders", aio_pika.ExchangeType.TOPIC, durable=True
                )
                queue = await channel.declare_queue("processor-service.order.created", durable=True)
                await queue.bind(exchange, routing_key="order.created")

                print("RabbitMQ consumer started, waiting for messages...")

                async with queue.iterator() as queue_iter:
                    async for message in queue_iter:
                        async with message.process():
                            await consumer.handle_message(message.body.decode())
        except Exception as e:
            print(f"RabbitMQ consumer error: {e}, retrying in 5s...")
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database, start background workers, and cleanup on shutdown."""
    global engine, background_tasks

    # Create engine
    engine = db.get_engine()

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(db.metadata.create_all)

    # Start background tasks
    task1 = asyncio.create_task(run_outbox_publisher())
    task2 = asyncio.create_task(run_rabbitmq_consumer())
    background_tasks.add(task1)
    background_tasks.add(task2)

    print("Processor service started with background workers")

    yield

    # Cleanup: cancel background tasks
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)

    # Dispose engine
    if engine:
        await engine.dispose()


app = FastAPI(title="Processor Service", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"service": get_service_name(), "status": "ok"}
