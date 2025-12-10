import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncEngine

from app.schemas import CreateOrderRequest, OrderResponse
from app.errors import validation_error_handler, generic_error_handler, request_validation_error_handler
from application.use_cases import CreateOrderCommand, CreateOrderUseCase
from domain.order import ItemLine, ValidationError
from infrastructure import db
from infrastructure.message_bus import OutboxPublisher, OrderProcessedConsumer
import aio_pika
from fastapi.exceptions import RequestValidationError


def get_service_name() -> str:
    return os.getenv("APP__SERVICE_NAME", "order-service")


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
    """Background task: consume order.processed events from RabbitMQ."""
    if not engine:
        return

    rabbitmq_url = os.getenv("APP__RABBITMQ_URL", "amqp://guest:guest@localhost/")
    consumer = OrderProcessedConsumer(engine)

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
                queue = await channel.declare_queue("order-service.order.processed", durable=True)
                await queue.bind(exchange, routing_key="order.processed")

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

    print("Order service started with background workers")

    yield

    # Cleanup: cancel background tasks
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)

    # Dispose engine
    if engine:
        await engine.dispose()


app = FastAPI(title="Order Service", version="0.1.0", lifespan=lifespan)

# Register error handlers
app.add_exception_handler(ValidationError, validation_error_handler)
app.add_exception_handler(RequestValidationError, request_validation_error_handler)
app.add_exception_handler(Exception, generic_error_handler)


@app.get("/health")
async def health() -> dict:
    return {"service": get_service_name(), "status": "ok"}


@app.post("/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(request: CreateOrderRequest) -> OrderResponse:
    """Create a new order (idempotent - returns existing if already created)."""
    if not engine:
        raise HTTPException(status_code=500, detail="Database not initialized")

    # Convert request to domain objects
    items = [
        ItemLine(sku=item.sku, quantity=item.quantity, price=item.price)
        for item in request.items
    ]

    # Create command
    command = CreateOrderCommand(
        order_id=request.order_id,
        customer_id=request.customer_id,
        items=items
    )

    # Execute use case (handles idempotency internally)
    # Let exceptions bubble up to global handlers
    uow = db.SqlAlchemyUnitOfWork(engine)
    use_case = CreateOrderUseCase(uow)
    order = await use_case.execute(command)

    # Return response
    return OrderResponse(
        order_id=order.order_id,
        customer_id=order.customer_id,
        status=order.status,
        total_amount=order.total_amount,
        version=order.version
    )


@app.get("/orders/{order_id}", response_model=OrderResponse)
async def get_order(order_id: str) -> OrderResponse:
    """Get order details by ID."""
    if not engine:
        raise HTTPException(status_code=500, detail="Database not initialized")

    # Query order from database
    uow = db.SqlAlchemyUnitOfWork(engine)
    async with uow:
        order = await uow.orders.get(order_id)

    # Check if order exists
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")

    # Return response
    return OrderResponse(
        order_id=order.order_id,
        customer_id=order.customer_id,
        status=order.status,
        total_amount=order.total_amount,
        version=order.version
    )
