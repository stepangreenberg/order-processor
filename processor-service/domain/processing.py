from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


class ValidationError(ValueError):
    """Raised when processing input is invalid."""


@dataclass
class ProcessingResult:
    status: str
    reason: Optional[str] = None


class ProcessingState:
    def __init__(
        self,
        order_id: str,
        version: int = 0,
        status: str = "received",
        attempt_count: int = 0,
        last_error: Optional[str] = None,
    ):
        self.order_id = order_id
        self.version = version
        self.status = status
        self.attempt_count = attempt_count
        self.last_error = last_error

    def apply_order_created(
        self,
        items: list[str],
        amount: float,
        version: int = 1,
        random_func: Callable[[], float] | None = None,
    ) -> ProcessingResult:
        """
        Apply order.created to this processing state.
        Deduplicates by version: if incoming version <= current -> ignore.
        """
        if version <= self.version:
            return ProcessingResult(status="ignored", reason="stale_version")

        random_func = random_func or (lambda: 0.5)
        self.version = version
        self.attempt_count += 1

        embargo_items = {"pineapple_pizza", "teapot"}
        if any(item in embargo_items for item in items):
            self.status = "failed"
            self.last_error = "Pineapple/teapot embargo"
            return ProcessingResult(status="failed", reason=self.last_error)

        if any(item == "potato" for item in items):
            self.status = "failed"
            self.last_error = "Too fatty food"
            return ProcessingResult(status="failed", reason=self.last_error)

        roll = random_func()
        if roll <= 0.6:
            self.status = "done"
            self.last_error = None
            return ProcessingResult(status="success")

        self.status = "failed"
        self.last_error = "Random failure"
        return ProcessingResult(status="failed", reason=self.last_error)
