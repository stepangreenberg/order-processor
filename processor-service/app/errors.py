"""Error handling and response models."""
from typing import Optional
from pydantic import BaseModel
from fastapi import Request, status
from fastapi.responses import JSONResponse

from infrastructure.logging import get_logger


logger = get_logger("processor-service")


class ErrorResponse(BaseModel):
    """Standard error response format."""
    status_code: int
    detail: str
    error_type: Optional[str] = None


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
