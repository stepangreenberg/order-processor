"""Error handling and response models."""
from typing import Optional
from pydantic import BaseModel
from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from domain.order import ValidationError as DomainValidationError
from infrastructure.logging import get_logger


logger = get_logger("order-service")


class ErrorResponse(BaseModel):
    """Standard error response format."""
    status_code: int
    detail: str
    error_type: Optional[str] = None


async def validation_error_handler(request: Request, exc: DomainValidationError) -> JSONResponse:
    """Handle domain validation errors with 400 status."""
    logger.warning(
        f"Validation error: {exc}",
        path=request.url.path,
        error=str(exc)
    )

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc), "error_type": "ValidationError"}
    )


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle generic exceptions with 500 status without exposing internals."""
    logger.error(
        f"Internal server error: {type(exc).__name__}",
        path=request.url.path,
        exc_info=True
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "error_type": "InternalServerError"
        }
    )


async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle FastAPI request validation errors with detailed messages."""
    logger.warning(
        "Request validation failed",
        path=request.url.path,
        errors=exc.errors()
    )

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": exc.errors(), "error_type": "RequestValidationError"}
    )
