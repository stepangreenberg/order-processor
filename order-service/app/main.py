import os
from fastapi import FastAPI


def get_service_name() -> str:
    return os.getenv("APP__SERVICE_NAME", "order-service")


app = FastAPI(title="Order Service", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"service": get_service_name(), "status": "ok"}
