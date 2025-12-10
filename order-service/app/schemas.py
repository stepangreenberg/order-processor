"""Pydantic schemas for HTTP API requests and responses."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ItemLineRequest(BaseModel):
    """Item line in an order request."""
    sku: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)
    price: float = Field(..., gt=0)


class CreateOrderRequest(BaseModel):
    """Request body for creating an order."""
    order_id: str = Field(..., min_length=1)
    customer_id: str = Field(..., min_length=1)
    items: list[ItemLineRequest] = Field(..., min_length=1)

    @field_validator('items')
    @classmethod
    def items_not_empty(cls, v):
        if not v:
            raise ValueError('items cannot be empty')
        return v


class OrderResponse(BaseModel):
    """Response for order endpoints."""
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    customer_id: str
    status: str
    total_amount: float
    version: int
