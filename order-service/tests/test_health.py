import os

from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok(monkeypatch):
    monkeypatch.setenv("APP__SERVICE_NAME", "order-service")
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "order-service"
    assert body["status"] == "ok"
