import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from testcontainers.postgres import PostgresContainer

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
async def test_get_order_success(initialized_app):
    """Test retrieving an existing order."""
    async with AsyncClient(transport=ASGITransport(app=initialized_app), base_url="http://test") as client:
        # First, create an order
        create_response = await client.post("/orders", json={
            "order_id": "ord-get-test",
            "customer_id": "cust-123",
            "items": [
                {"sku": "widget", "quantity": 3, "price": 10.0}
            ]
        })
        assert create_response.status_code == 201

        # Now retrieve it
        response = await client.get("/orders/ord-get-test")

    assert response.status_code == 200
    data = response.json()
    assert data["order_id"] == "ord-get-test"
    assert data["customer_id"] == "cust-123"
    assert data["status"] == "pending"
    assert data["total_amount"] == 30.0
    assert data["version"] == 1


@pytest.mark.asyncio
async def test_get_order_not_found(initialized_app):
    """Test retrieving a non-existent order returns 404."""
    async with AsyncClient(transport=ASGITransport(app=initialized_app), base_url="http://test") as client:
        response = await client.get("/orders/non-existent-order")

    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
