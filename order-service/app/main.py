import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncEngine

from app.schemas import CreateOrderRequest, OrderResponse
from application.use_cases import CreateOrderCommand, CreateOrderUseCase
from domain.order import ItemLine, ValidationError
from infrastructure import db


def get_service_name() -> str:
    return os.getenv("APP__SERVICE_NAME", "order-service")


# Global engine (initialized at startup)
engine: AsyncEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and cleanup on shutdown."""
    global engine
    # Create engine
    engine = db.get_engine()
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(db.metadata.create_all)
    yield
    # Cleanup
    if engine:
        await engine.dispose()


app = FastAPI(title="Order Service", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"service": get_service_name(), "status": "ok"}


@app.post("/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(request: CreateOrderRequest) -> OrderResponse:
    """Create a new order (idempotent - returns existing if already created)."""
    if not engine:
        raise HTTPException(status_code=500, detail="Database not initialized")

    try:
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

    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/orders/{order_id}", response_model=OrderResponse)
async def get_order(order_id: str) -> OrderResponse:
    """Get order details by ID."""
    if not engine:
        raise HTTPException(status_code=500, detail="Database not initialized")

    try:
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

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
