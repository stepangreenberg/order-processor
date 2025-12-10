import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from testcontainers.postgres import PostgresContainer
from sqlalchemy import text

# Import app components
from infrastructure import db

postgres_container = None


@pytest.fixture(scope="module", autouse=True)
def setup_test_database():
    """Setup PostgreSQL container for tests."""
    global postgres_container
    postgres_container = PostgresContainer("postgres:15-alpine")
    postgres_container.start()

    # Set environment variable for the app
    dsn = postgres_container.get_connection_url(driver="asyncpg")
    os.environ["APP__DB_DSN"] = dsn

    yield

    # Cleanup
    postgres_container.stop()


@pytest_asyncio.fixture
async def initialized_app():
    """Initialize app with database."""
    # Import app AFTER env vars are set
    from app import main

    # Create engine and initialize database
    main.engine = db.get_engine()
    async with main.engine.begin() as conn:
        await conn.run_sync(db.metadata.create_all)

    yield main.app

    # Cleanup
    if main.engine:
        await main.engine.dispose()


@pytest.mark.asyncio
async def test_create_order_success(initialized_app):
    """Test successful order creation via HTTP API."""
    async with AsyncClient(transport=ASGITransport(app=initialized_app), base_url="http://test") as client:
        response = await client.post("/orders", json={
            "order_id": "ord-456",
            "customer_id": "cust-789",
            "items": [
                {"sku": "laptop", "quantity": 1, "price": 1200.0},
                {"sku": "mouse", "quantity": 2, "price": 25.0}
            ]
        })

    if response.status_code != 201:
        print(f"Error response: {response.text}")

    assert response.status_code == 201
    data = response.json()
    assert data["order_id"] == "ord-456"
    assert data["customer_id"] == "cust-789"
    assert data["status"] == "pending"
    assert data["total_amount"] == 1250.0


@pytest.mark.asyncio
async def test_create_order_invalid_items(initialized_app):
    """Test order creation with invalid items returns 422."""
    async with AsyncClient(transport=ASGITransport(app=initialized_app), base_url="http://test") as client:
        response = await client.post("/orders", json={
            "order_id": "ord-999",
            "customer_id": "cust-111",
            "items": []  # Empty items - should fail validation
        })

    assert response.status_code == 422  # FastAPI validation error
    data = response.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_create_order_idempotency(initialized_app):
    """Test that creating the same order twice returns the existing order."""
    order_data = {
        "order_id": "ord-idempotent",
        "customer_id": "cust-222",
        "items": [{"sku": "book", "quantity": 1, "price": 15.0}]
    }

    async with AsyncClient(transport=ASGITransport(app=initialized_app), base_url="http://test") as client:
        # First creation
        response1 = await client.post("/orders", json=order_data)
        assert response1.status_code == 201

        # Second creation (should be idempotent - returns same order)
        response2 = await client.post("/orders", json=order_data)
        assert response2.status_code == 201  # Still 201, but idempotent
        data = response2.json()
        assert data["order_id"] == "ord-idempotent"
        # Verify it's the same order (version unchanged)
        assert data["version"] == 1
