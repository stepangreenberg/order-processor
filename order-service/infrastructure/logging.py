"""Structured logging for microservices."""
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Optional


class StructuredLogger:
    """Logger that outputs structured JSON logs."""

    def __init__(self, service_name: str, level: int = logging.INFO):
        self.service_name = service_name
        self.logger = logging.getLogger(service_name)
        self.logger.setLevel(level)

        # Remove existing handlers to avoid duplicates
        self.logger.handlers.clear()

        # Add JSON formatter handler
        handler = self._setup_handler(sys.stdout)
        self.logger.addHandler(handler)

    def _setup_handler(self, stream):
        """Setup handler with JSON formatter."""
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter(self.service_name))
        return handler

    def info(self, message: str, **extra):
        """Log info level message with extra context."""
        self.logger.info(message, extra=extra)

    def warning(self, message: str, **extra):
        """Log warning level message with extra context."""
        self.logger.warning(message, extra=extra)

    def error(self, message: str, exc_info: bool = False, **extra):
        """Log error level message with optional exception info."""
        self.logger.error(message, exc_info=exc_info, extra=extra)


class JsonFormatter(logging.Formatter):
    """Custom formatter that outputs logs as JSON."""

    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "message": record.getMessage(),
        }

        # Add extra fields if present
        if hasattr(record, "extra"):
            log_entry.update(record.extra)

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self._format_exception(record.exc_info)

        return json.dumps(log_entry)

    def _format_exception(self, exc_info) -> str:
        """Format exception info as string."""
        return "".join(traceback.format_exception(*exc_info))


def get_logger(service_name: str, level: int = logging.INFO) -> StructuredLogger:
    """Get a structured logger for the service."""
    return StructuredLogger(service_name=service_name, level=level)
