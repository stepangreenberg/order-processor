from __future__ import annotations

from dataclasses import dataclass
from typing import List


class ValidationError(ValueError):
    """Raised when order data is invalid."""


@dataclass(frozen=True)
class ItemLine:
    sku: str
    quantity: int
    price: float

    def total(self) -> float:
        return self.quantity * self.price


class Order:
    def __init__(
        self,
        order_id: str,
        customer_id: str,
        items: List[ItemLine],
        status: str = "pending",
        version: int = 1,
    ):
        self.order_id = order_id
        self.customer_id = customer_id
        self.items = items
        self.status = status
        self.version = version
        self.total_amount = sum(item.total() for item in items)

    @classmethod
    def create(cls, order_id: str, customer_id: str, items: List[ItemLine]) -> "Order":
        if not items:
            raise ValidationError("Order must contain at least one item")
        if any(item.quantity <= 0 for item in items):
            raise ValidationError("Item quantity must be positive")
        if any(item.price <= 0 for item in items):
            raise ValidationError("Item price must be positive")

        return cls(order_id=order_id, customer_id=customer_id, items=items)

    @classmethod
    def hydrate(
        cls,
        order_id: str,
        customer_id: str,
        items: List[ItemLine],
        status: str,
        version: int,
        total_amount: float,
    ) -> "Order":
        obj = cls.__new__(cls)
        obj.order_id = order_id
        obj.customer_id = customer_id
        obj.items = items
        obj.status = status
        obj.version = version
        obj.total_amount = total_amount
        return obj
