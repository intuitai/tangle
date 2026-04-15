# tests/test_metrics.py

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tangle.config import TangleConfig
from tangle.metrics import TangleMetrics
from tangle.monitor import TangleMonitor
from tangle.types import (
    Cycle,
    Detection,
    DetectionType,
    EventType,
    LivelockPattern,
    Severity,
)

from .conftest import FakeClock, make_event

if TYPE_CHECKING:
    from prometheus_client import CollectorRegistry


# ---------------------------------------------------------------------------
# Unit tests for TangleMetrics
# ---------------------------------------------------------------------------


class TestTangleMetrics:
    def test_registry_isolation(self) -> None:
        """Each TangleMetrics instance uses its own registry."""
        m1 = TangleMetrics()
        m2 = TangleMetrics()
        assert m1.registry is not m2.registry

    def test_record_detection_increments_counter(self) -> None:
        m = TangleMetrics()
        detection = Detection(
            type=DetectionType.DEADLOCK,
            severity=Severity.CRITICAL,
            cycle=Cycle(agents=["A", "B"], workflow_id="wf-1"),
        )
        m.record_detection(detection)
        m.record_detection(detection)

        val = m.detections_total.labels(type="deadlock", severity="critical")._value.get()
        assert val == 2.0

    def test_record_detection_observes_cycle_depth(self) -> None:
        m = TangleMetrics()
        detection = Detection(
            type=DetectionType.DEADLOCK,
            severity=Severity.CRITICAL,
            cycle=Cycle(agents=["A", "B", "C"], workflow_id="wf-1"),
        )
        m.record_detection(detection)

        # Histogram sum should equal the observed value (3 agents)
        assert m.cycle_depth.observe  # exists
        sample_sum = _histogram_sum(m.registry, "tangle_cycle_depth")
        assert sample_sum == 3.0

    def test_record_detection_livelock_no_cycle_depth(self) -> None:
        """Livelock detections don't observe cycle_depth."""
        m = TangleMetrics()
        detection = Detection(
            type=DetectionType.LIVELOCK,
            severity=Severity.CRITICAL,
            livelock=LivelockPattern(
                agents=["A", "B"],
                pattern_length=2,
                repeat_count=5,
                workflow_id="wf-1",
            ),
        )
        m.record_detection(detection)

        val = m.detections_total.labels(type="livelock", severity="critical")._value.get()
        assert val == 1.0
        # No cycle depth observed
        sample_sum = _histogram_sum(m.registry, "tangle_cycle_depth")
        assert sample_sum == 0.0

    def test_record_event_increments_per_type(self) -> None:
        m = TangleMetrics()
        m.record_event("wait_for")
        m.record_event("wait_for")
        m.record_event("send")

        assert m.events_total.labels(type="wait_for")._value.get() == 2.0
        assert m.events_total.labels(type="send")._value.get() == 1.0

    def test_set_active_workflows(self) -> None:
        m = TangleMetrics()
        m.set_active_workflows(3)
        assert m.active_workflows._value.get() == 3.0
        m.set_active_workflows(1)
        assert m.active_workflows._value.get() == 1.0


# ---------------------------------------------------------------------------
# Integration tests: metrics through TangleMonitor
# ---------------------------------------------------------------------------


class TestMonitorMetricsIntegration:
    def _make_monitor(self, clock: FakeClock) -> TangleMonitor:
        config = TangleConfig(
            cycle_check_interval=999_999.0,
            metrics_enabled=True,
        )
        return TangleMonitor(config=config, clock=clock)

    def test_metrics_disabled_by_default(self) -> None:
        config = TangleConfig(cycle_check_interval=999_999.0)
        mon = TangleMonitor(config=config, clock=FakeClock())
        assert mon.metrics is None

    def test_metrics_enabled(self) -> None:
        mon = self._make_monitor(FakeClock())
        assert mon.metrics is not None

    def test_event_counter_tracks_events(self) -> None:
        clock = FakeClock()
        mon = self._make_monitor(clock)
        mon.process_event(make_event(EventType.REGISTER, from_agent="A", timestamp=clock()))
        clock.advance(1)
        mon.process_event(
            make_event(EventType.WAIT_FOR, from_agent="A", to_agent="B", timestamp=clock())
        )

        assert mon.metrics is not None
        assert mon.metrics.events_total.labels(type="register")._value.get() == 1.0
        assert mon.metrics.events_total.labels(type="wait_for")._value.get() == 1.0

    def test_deadlock_detection_records_metrics(self) -> None:
        clock = FakeClock()
        mon = self._make_monitor(clock)

        # Register two agents
        mon.process_event(make_event(EventType.REGISTER, from_agent="A", timestamp=clock()))
        clock.advance(1)
        mon.process_event(make_event(EventType.REGISTER, from_agent="B", timestamp=clock()))
        clock.advance(1)

        # Create deadlock: A waits for B, B waits for A
        mon.process_event(
            make_event(EventType.WAIT_FOR, from_agent="A", to_agent="B", timestamp=clock())
        )
        clock.advance(1)
        mon.process_event(
            make_event(EventType.WAIT_FOR, from_agent="B", to_agent="A", timestamp=clock())
        )

        assert mon.metrics is not None
        val = mon.metrics.detections_total.labels(type="deadlock", severity="critical")._value.get()
        assert val == 1.0

        # Cycle depth = 2 agents
        sample_sum = _histogram_sum(mon.metrics.registry, "tangle_cycle_depth")
        assert sample_sum == 2.0

    def test_active_workflows_gauge(self) -> None:
        clock = FakeClock()
        mon = self._make_monitor(clock)

        mon.process_event(
            make_event(EventType.REGISTER, workflow_id="wf-1", from_agent="A", timestamp=clock())
        )
        clock.advance(1)
        assert mon.metrics is not None
        assert mon.metrics.active_workflows._value.get() == 1.0

        mon.process_event(
            make_event(EventType.REGISTER, workflow_id="wf-2", from_agent="B", timestamp=clock())
        )
        clock.advance(1)
        assert mon.metrics.active_workflows._value.get() == 2.0

    def test_livelock_detection_records_metrics(self) -> None:
        clock = FakeClock()
        mon = self._make_monitor(clock)

        mon.process_event(make_event(EventType.REGISTER, from_agent="A", timestamp=clock()))
        clock.advance(1)
        mon.process_event(make_event(EventType.REGISTER, from_agent="B", timestamp=clock()))
        clock.advance(1)

        # Send repeated ping-pong messages to trigger livelock
        for _i in range(30):
            mon.process_event(
                make_event(
                    EventType.SEND,
                    from_agent="A",
                    to_agent="B",
                    message_body=b"request",
                    timestamp=clock(),
                )
            )
            clock.advance(1)
            mon.process_event(
                make_event(
                    EventType.SEND,
                    from_agent="B",
                    to_agent="A",
                    message_body=b"reject",
                    timestamp=clock(),
                )
            )
            clock.advance(1)

        assert mon.metrics is not None
        val = mon.metrics.detections_total.labels(type="livelock", severity="critical")._value.get()
        assert val >= 1.0


# ---------------------------------------------------------------------------
# Server endpoint test
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    @pytest.fixture()
    def client(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from tangle.server.routes import router

        clock = FakeClock()
        config = TangleConfig(cycle_check_interval=999_999.0, metrics_enabled=True)
        monitor = TangleMonitor(config=config, clock=clock)

        app = FastAPI()
        app.include_router(router, prefix="/v1")
        app.state.monitor = monitor

        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.fixture()
    def client_no_metrics(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from tangle.server.routes import router

        clock = FakeClock()
        config = TangleConfig(cycle_check_interval=999_999.0, metrics_enabled=False)
        monitor = TangleMonitor(config=config, clock=clock)

        app = FastAPI()
        app.include_router(router, prefix="/v1")
        app.state.monitor = monitor

        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    async def test_metrics_endpoint_returns_prometheus_format(self, client) -> None:
        resp = await client.get("/v1/metrics")
        assert resp.status_code == 200
        body = resp.text
        assert "tangle_detections_total" in body or "tangle_events_total" in body
        assert "text/plain" in resp.headers["content-type"]

    async def test_metrics_endpoint_disabled(self, client_no_metrics) -> None:
        resp = await client_no_metrics.get("/v1/metrics")
        assert resp.status_code == 404
        assert "not enabled" in resp.text.lower()

    async def test_metrics_endpoint_after_events(self, client) -> None:
        # Send an event through the API
        await client.post(
            "/v1/events",
            json={
                "type": "register",
                "workflow_id": "wf-1",
                "from_agent": "A",
            },
        )
        resp = await client.get("/v1/metrics")
        assert resp.status_code == 200
        assert "tangle_events_total" in resp.text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _histogram_sum(registry: CollectorRegistry, name: str) -> float:
    """Extract the _sum value from a histogram in the given registry."""
    for metric in registry.collect():
        if metric.name == name:
            for sample in metric.samples:
                if sample.name == f"{name}_sum":
                    return sample.value
    return 0.0
