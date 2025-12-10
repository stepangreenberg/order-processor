"""Tests for error handling middleware."""
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from app import main
from domain.order import ValidationError as DomainValidationError


@pytest.fixture
def test_client():
    """Create test client with mocked engine."""
    # Mock the engine
    main.engine = MagicMock()
    # raise_server_exceptions=False allows us to test error responses
    client = TestClient(main.app, raise_server_exceptions=False)
    yield client
    main.engine = None


def test_domain_validation_error_returns_400(test_client):
    """Domain ValidationError should return 400."""
    from application.use_cases import CreateOrderUseCase

    async def mock_execute(self, command):
        raise DomainValidationError("Item quantity must be positive")

    CreateOrderUseCase.execute = mock_execute

    response = test_client.post(
        "/orders",
        json={
            "order_id": "ord-1",
            "customer_id": "cust-1",
            "items": [
                {"sku": "laptop", "quantity": 1, "price": 100.0}
            ]
        }
    )

    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "quantity" in data["detail"].lower()
    # Our handler includes error_type
    assert "error_type" in data
    assert data["error_type"] == "ValidationError"


def test_validation_error_returns_400(test_client):
    """Pydantic validation errors should return 400 with error details."""
    response = test_client.post(
        "/orders",
        json={
            "order_id": "ord-1",
            "customer_id": "cust-1",
            "items": []  # Invalid: empty items
        }
    )

    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert isinstance(data["detail"], list)  # Pydantic validation errors
    assert "error_type" in data


def test_not_found_returns_404(test_client):
    """Missing resource should return 404."""
    from infrastructure import db

    async def mock_get(self, order_id):
        return None

    original_get = db.OrderRepository.get
    db.OrderRepository.get = mock_get

    try:
        response = test_client.get("/orders/non-existent-order")

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert "not found" in data["detail"].lower()
    finally:
        db.OrderRepository.get = original_get


def test_generic_exception_returns_500(test_client):
    """Generic exceptions should return 500 without exposing internals."""
    from application.use_cases import CreateOrderUseCase

    async def mock_execute(self, command):
        raise RuntimeError("Some internal error")

    CreateOrderUseCase.execute = mock_execute

    response = test_client.post(
        "/orders",
        json={
            "order_id": "ord-1",
            "customer_id": "cust-1",
            "items": [{"sku": "laptop", "quantity": 1, "price": 100.0}]
        }
    )

    assert response.status_code == 500
    data = response.json()
    assert "detail" in data
    # Should not expose internal error message
    assert data["detail"] == "Internal server error"
    assert data["error_type"] == "InternalServerError"
    assert "RuntimeError" not in str(data)


def test_error_response_format():
    """Error responses should have consistent format."""
    from app.errors import ErrorResponse

    error = ErrorResponse(
        status_code=400,
        detail="Test error",
        error_type="ValidationError"
    )

    assert error.status_code == 400
    assert error.detail == "Test error"
    assert error.error_type == "ValidationError"


def test_malformed_json_returns_400(test_client):
    """Malformed JSON should return 400 (RequestValidationError)."""
    response = test_client.post(
        "/orders",
        data="not-valid-json",
        headers={"Content-Type": "application/json"}
    )

    # FastAPI returns 400 for request validation errors
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
