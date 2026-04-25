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

        self.workflows_retained = Gauge(
            "tangle_workflows_retained",
            "Workflows currently tracked by the retention manager",
            registry=self.registry,
        )

        self.workflows_evicted_total = Counter(
            "tangle_workflows_evicted_total",
            "Workflows evicted by the retention manager",
            ["reason"],
            registry=self.registry,
        )

        self.events_retained = Gauge(
            "tangle_events_retained",
            "Events currently held in the in-memory store",
            registry=self.registry,
        )

        self.events_evicted_total = Counter(
            "tangle_events_evicted_total",
            "Events dropped from the in-memory store due to capacity",
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

    def set_workflows_retained(self, count: int) -> None:
        self.workflows_retained.set(count)

    def record_workflow_evictions(self, ttl: int = 0, capacity: int = 0) -> None:
        if ttl:
            self.workflows_evicted_total.labels(reason="ttl").inc(ttl)
        if capacity:
            self.workflows_evicted_total.labels(reason="capacity").inc(capacity)

    def set_events_retained(self, count: int) -> None:
        self.events_retained.set(count)

    def record_event_evictions(self, count: int) -> None:
        if count:
            self.events_evicted_total.inc(count)
