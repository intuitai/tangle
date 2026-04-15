# src/tangle/metrics.py

"""Prometheus metrics for Tangle.

Requires the ``prometheus-client`` package (install via ``pip install tangle-detect[metrics]``).
Uses a dedicated :class:`CollectorRegistry` so that metrics are isolated per
``TangleMonitor`` instance and don't leak between tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

if TYPE_CHECKING:
    from tangle.types import Detection


class TangleMetrics:
    """Holds all Prometheus metric objects for a single TangleMonitor."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()

        self.detections_total = Counter(
            "tangle_detections_total",
            "Total detections raised",
            ["type", "severity"],
            registry=self.registry,
        )

        self.cycle_depth = Histogram(
            "tangle_cycle_depth",
            "Number of agents involved in detected cycles",
            buckets=[2, 3, 4, 5, 6, 8, 10, 15, 20],
            registry=self.registry,
        )

        self.active_workflows = Gauge(
            "tangle_active_workflows",
            "Number of workflows with at least one registered agent",
            registry=self.registry,
        )

        self.events_total = Counter(
            "tangle_events_total",
            "Total events processed",
            ["type"],
            registry=self.registry,
        )

    def record_detection(self, detection: Detection) -> None:
        """Update counters and histograms for a new detection."""
        self.detections_total.labels(
            type=detection.type.value,
            severity=detection.severity.value,
        ).inc()

        if detection.cycle:
            self.cycle_depth.observe(len(detection.cycle.agents))

    def record_event(self, event_type: str) -> None:
        """Increment the per-type event counter."""
        self.events_total.labels(type=event_type).inc()

    def set_active_workflows(self, count: int) -> None:
        """Set the active-workflows gauge to the current value."""
        self.active_workflows.set(count)
