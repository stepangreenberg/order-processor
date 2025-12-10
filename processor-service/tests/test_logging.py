"""Tests for structured logging."""
import json
import pytest
from infrastructure.logging import get_logger, StructuredLogger


def test_logger_formats_json_with_service_name():
    """Logger should format messages as JSON with service name."""
    logger = get_logger("test-service")

    # Should return a StructuredLogger instance
    assert isinstance(logger, StructuredLogger)
    assert logger.service_name == "test-service"


def test_structured_logger_info_includes_metadata():
    """Info log should include level, message, service, and timestamp."""
    logger = StructuredLogger(service_name="test-service")

    # Capture log output
    import io
    import sys
    captured_output = io.StringIO()

    # Mock the handler to capture output
    handler = logger._setup_handler(captured_output)
    logger.logger.handlers = [handler]

    logger.info("Test message", extra={"request_id": "req-123", "order_id": "ord-456"})

    # Parse the JSON output
    output = captured_output.getvalue().strip()
    log_entry = json.loads(output)

    assert log_entry["level"] == "INFO"
    assert log_entry["message"] == "Test message"
    assert log_entry["service"] == "test-service"
    assert "timestamp" in log_entry
    assert log_entry["request_id"] == "req-123"
    assert log_entry["order_id"] == "ord-456"


def test_structured_logger_error_includes_exc_info():
    """Error log should include exception info when provided."""
    logger = StructuredLogger(service_name="test-service")

    import io
    captured_output = io.StringIO()
    handler = logger._setup_handler(captured_output)
    logger.logger.handlers = [handler]

    try:
        raise ValueError("Test error")
    except ValueError:
        logger.error("Error occurred", exc_info=True)

    output = captured_output.getvalue().strip()
    log_entry = json.loads(output)

    assert log_entry["level"] == "ERROR"
    assert log_entry["message"] == "Error occurred"
    assert "exception" in log_entry
    assert "ValueError: Test error" in log_entry["exception"]


def test_structured_logger_warning():
    """Warning log should work correctly."""
    logger = StructuredLogger(service_name="test-service")

    import io
    captured_output = io.StringIO()
    handler = logger._setup_handler(captured_output)
    logger.logger.handlers = [handler]

    logger.warning("Warning message")

    output = captured_output.getvalue().strip()
    log_entry = json.loads(output)

    assert log_entry["level"] == "WARNING"
    assert log_entry["message"] == "Warning message"
