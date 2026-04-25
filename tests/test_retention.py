# tests/test_retention.py

from __future__ import annotations

from tangle.config import TangleConfig
from tangle.detector.livelock import LivelockDetector
from tangle.graph.wfg import WaitForGraph
from tangle.monitor import TangleMonitor
from tangle.retention import RetentionManager, detection_belongs_to
from tangle.store.memory import MemoryStore
from tangle.types import (
    AgentStatus,
    Cycle,
    Detection,
    DetectionType,
    EventType,
    LivelockPattern,
    Severity,
)

from .conftest import FakeClock, make_event


def _new_manager(
    clock: FakeClock,
    *,
    completed_ttl: float = 0.0,
    cap: int = 0,
) -> tuple[RetentionManager, WaitForGraph, LivelockDetector]:
    graph = WaitForGraph()
    livelock = LivelockDetector()
    mgr = RetentionManager(
        graph=graph,
        livelock_detector=livelock,
        clock=clock,
        completed_ttl=completed_ttl,
        max_active_workflows=cap,
    )
    return mgr, graph, livelock


def _terminal_workflow(graph: WaitForGraph, wf: str, agent: str = "A") -> None:
    graph.register_agent(agent, wf, timestamp=0.0)
    graph.set_state(agent, AgentStatus.COMPLETED, workflow_id=wf)


class TestRetentionManagerUnit:
    def test_disabled_when_ttl_zero(self) -> None:
        clock = FakeClock()
        mgr, graph, _ = _new_manager(clock)
        _terminal_workflow(graph, "wf-1")
        mgr.note_event(make_event(EventType.COMPLETE, workflow_id="wf-1", timestamp=clock()))

        clock.advance(10_000)
        evicted: list[str] = []
        result = mgr.sweep(on_evict=evicted.append)

        assert evicted == []
        assert result.evicted_ttl == 0
        assert result.retained_workflows == 1

    def test_ttl_evicts_terminal_workflow(self) -> None:
        clock = FakeClock()
        mgr, graph, _ = _new_manager(clock, completed_ttl=60.0)
        _terminal_workflow(graph, "wf-1")
        mgr.note_event(make_event(EventType.COMPLETE, workflow_id="wf-1", timestamp=clock()))

        clock.advance(120)
        evicted: list[str] = []
        result = mgr.sweep(on_evict=evicted.append)

        assert evicted == ["wf-1"]
        assert result.evicted_ttl == 1
        assert result.retained_workflows == 0
        assert graph.agents_in_workflow("wf-1") == []

    def test_ttl_does_not_evict_non_terminal(self) -> None:
        """Stuck deadlocked workflows must not age out — that's what we detect."""
        clock = FakeClock()
        mgr, graph, _ = _new_manager(clock, completed_ttl=10.0)
        graph.register_agent("A", "wf-1", timestamp=0.0)
        graph.set_state("A", AgentStatus.WAITING, workflow_id="wf-1")
        mgr.note_event(make_event(EventType.WAIT_FOR, workflow_id="wf-1", timestamp=clock()))

        clock.advance(1_000)
        evicted: list[str] = []
        result = mgr.sweep(on_evict=evicted.append)

        assert evicted == []
        assert result.evicted_ttl == 0
        assert result.retained_workflows == 1

    def test_capacity_evicts_terminal_first(self) -> None:
        clock = FakeClock()
        mgr, graph, _ = _new_manager(clock, cap=2)
        for i, wf in enumerate(["wf-old", "wf-mid", "wf-new"]):
            _terminal_workflow(graph, wf, agent=f"agent-{i}")
            clock.advance(5)
            mgr.note_event(make_event(EventType.COMPLETE, workflow_id=wf, timestamp=clock()))

        evicted: list[str] = []
        result = mgr.sweep(on_evict=evicted.append)

        assert result.evicted_capacity == 1
        assert evicted == ["wf-old"]
        assert result.retained_workflows == 2

    def test_capacity_records_overflow_when_no_terminal(self) -> None:
        clock = FakeClock()
        mgr, graph, _ = _new_manager(clock, cap=1)
        for i, wf in enumerate(["wf-1", "wf-2"]):
            graph.register_agent(f"a-{i}", wf, timestamp=0.0)
            graph.set_state(f"a-{i}", AgentStatus.WAITING, workflow_id=wf)
            mgr.note_event(make_event(EventType.WAIT_FOR, workflow_id=wf, timestamp=clock()))
            clock.advance(1)

        evicted: list[str] = []
        result = mgr.sweep(on_evict=evicted.append)

        assert evicted == []
        assert result.overflow_unresolved == 1
        assert result.retained_workflows == 2

    def test_forget_workflow_removes_tracking(self) -> None:
        clock = FakeClock()
        mgr, graph, _ = _new_manager(clock)
        _terminal_workflow(graph, "wf-1")
        mgr.note_event(make_event(EventType.COMPLETE, workflow_id="wf-1", timestamp=clock()))
        assert mgr.tracked_count() == 1

        mgr.forget_workflow("wf-1")
        assert mgr.tracked_count() == 0


class TestDetectionBelongsTo:
    def test_cycle_match(self) -> None:
        d = Detection(
            type=DetectionType.DEADLOCK,
            severity=Severity.CRITICAL,
            cycle=Cycle(workflow_id="wf-1"),
        )
        assert detection_belongs_to(d, "wf-1")
        assert not detection_belongs_to(d, "wf-2")

    def test_livelock_match(self) -> None:
        d = Detection(
            type=DetectionType.LIVELOCK,
            severity=Severity.CRITICAL,
            livelock=LivelockPattern(workflow_id="wf-x"),
        )
        assert detection_belongs_to(d, "wf-x")
        assert not detection_belongs_to(d, "wf-y")


class TestMemoryStoreBounds:
    def test_unbounded_by_default(self) -> None:
        store = MemoryStore()
        for i in range(50):
            store.record_event(make_event(EventType.SEND, timestamp=float(i)))
        assert store.event_count() == 50
        assert store.drain_evicted() == 0

    def test_evicts_when_capacity_exceeded(self) -> None:
        store = MemoryStore(max_events=5)
        for i in range(8):
            store.record_event(make_event(EventType.SEND, timestamp=float(i)))

        assert store.event_count() == 5
        assert store.drain_evicted() == 3
        # Drain resets the counter so metrics don't double-count.
        assert store.drain_evicted() == 0

    def test_evicted_events_are_oldest(self) -> None:
        store = MemoryStore(max_events=3)
        for i in range(5):
            store.record_event(make_event(EventType.SEND, workflow_id="wf-1", timestamp=float(i)))
        events = store.get_workflow_events("wf-1")
        assert [e.timestamp for e in events] == [2.0, 3.0, 4.0]


class TestMonitorRetentionIntegration:
    def _config(self, **overrides: float | int) -> TangleConfig:
        defaults: dict[str, float | int] = {
            "cycle_check_interval": 999_999.0,
            "metrics_enabled": True,
        }
        defaults.update(overrides)
        return TangleConfig(**defaults)  # type: ignore[arg-type]

    def test_sweep_evicts_completed_workflow_and_clears_state(self) -> None:
        clock = FakeClock()
        mon = TangleMonitor(
            config=self._config(retention_completed_workflow_ttl=30.0),
            clock=clock,
        )
        try:
            mon.register("wf-1", "A")
            mon.complete("wf-1", "A")
            clock.advance(31)
            mon.sweep_retention()

            snap = mon.snapshot(workflow_id="wf-1")
            assert snap.nodes == []
            assert mon._retention.tracked_count() == 0  # type: ignore[attr-defined]
            assert mon.metrics is not None
            assert mon.metrics.workflows_evicted_total.labels(reason="ttl")._value.get() == 1.0
            assert mon.metrics.workflows_retained._value.get() == 0.0
        finally:
            mon.stop()

    def test_sweep_does_not_evict_in_flight_workflow(self) -> None:
        clock = FakeClock()
        mon = TangleMonitor(
            config=self._config(retention_completed_workflow_ttl=10.0),
            clock=clock,
        )
        try:
            mon.register("wf-1", "A")
            mon.register("wf-1", "B")
            mon.wait_for("wf-1", "A", "B")
            clock.advance(1_000)
            mon.sweep_retention()

            snap = mon.snapshot(workflow_id="wf-1")
            assert set(snap.nodes) == {"A", "B"}
        finally:
            mon.stop()

    def test_eviction_drops_resolved_detections_for_workflow(self) -> None:
        clock = FakeClock()
        mon = TangleMonitor(
            config=self._config(retention_completed_workflow_ttl=10.0),
            clock=clock,
        )
        try:
            mon.register("wf-1", "A")
            mon.register("wf-1", "B")
            mon.wait_for("wf-1", "A", "B")
            mon.wait_for("wf-1", "B", "A")
            assert any(d.cycle and d.cycle.workflow_id == "wf-1" for d in mon._detections)  # type: ignore[attr-defined]

            mon.complete("wf-1", "A")
            mon.complete("wf-1", "B")
            clock.advance(60)
            mon.sweep_retention()

            assert not any(
                d.cycle and d.cycle.workflow_id == "wf-1"
                for d in mon._detections  # type: ignore[attr-defined]
            )
        finally:
            mon.stop()

    def test_event_eviction_metrics(self) -> None:
        clock = FakeClock()
        mon = TangleMonitor(
            config=self._config(max_events_in_memory=3),
            clock=clock,
        )
        try:
            for _ in range(7):
                mon.register("wf-1", "A")
                clock.advance(1)
            mon.sweep_retention()
            assert mon.metrics is not None
            assert mon.metrics.events_retained._value.get() == 3.0
            assert mon.metrics.events_evicted_total._value.get() == 4.0
        finally:
            mon.stop()
