"""Simple metrics tracking for Prometheus-compatible /metrics endpoint."""

from datetime import datetime, timezone
from typing import Dict


class MetricsCollector:
    """Simple in-memory metrics collector."""

    def __init__(self):
        self._counters: Dict[str, int] = {
            "events_published_total": 0,
            "events_failed_total": 0,
            "events_moved_to_dlq_total": 0,
            "orders_created_total": 0,
            "orders_processed_total": 0,
        }

    def increment(self, metric_name: str, value: int = 1) -> None:
        """Increment a counter metric."""
        if metric_name in self._counters:
            self._counters[metric_name] += value

    def get_prometheus_text(self) -> str:
        """Generate Prometheus-compatible text format."""
        lines = []
        lines.append("# HELP events_published_total Total number of events successfully published")
        lines.append("# TYPE events_published_total counter")
        lines.append(f"events_published_total {self._counters['events_published_total']}")
        lines.append("")

        lines.append("# HELP events_failed_total Total number of events that failed to publish")
        lines.append("# TYPE events_failed_total counter")
        lines.append(f"events_failed_total {self._counters['events_failed_total']}")
        lines.append("")

        lines.append("# HELP events_moved_to_dlq_total Total number of events moved to Dead Letter Queue")
        lines.append("# TYPE events_moved_to_dlq_total counter")
        lines.append(f"events_moved_to_dlq_total {self._counters['events_moved_to_dlq_total']}")
        lines.append("")

        lines.append("# HELP orders_created_total Total number of orders created")
        lines.append("# TYPE orders_created_total counter")
        lines.append(f"orders_created_total {self._counters['orders_created_total']}")
        lines.append("")

        lines.append("# HELP orders_processed_total Total number of orders processed")
        lines.append("# TYPE orders_processed_total counter")
        lines.append(f"orders_processed_total {self._counters['orders_processed_total']}")
        lines.append("")

        return "\n".join(lines)


# Global metrics instance
metrics = MetricsCollector()
