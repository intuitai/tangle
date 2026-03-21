# tests/store/conformance.py

"""
Store conformance test suite.

Any Store implementation must pass all tests in this module.
Call ``run_store_conformance(store_factory)`` where *store_factory* is a
zero-argument callable that returns a fresh store instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from tangle.types import (
    Cycle,
    Detection,
    DetectionType,
    Event,
    EventType,
    LivelockPattern,
    Severity,
)


def _make_deadlock_detection(workflow_id: str = "wf-1") -> Detection:
    return Detection(
        type=DetectionType.DEADLOCK,
        severity=Severity.CRITICAL,
        cycle=Cycle(
            agents=["A", "B"],
            workflow_id=workflow_id,
        ),
    )


def _make_livelock_detection(workflow_id: str = "wf-1") -> Detection:
    return Detection(
        type=DetectionType.LIVELOCK,
        severity=Severity.CRITICAL,
        livelock=LivelockPattern(
            agents=["A", "B"],
            pattern_length=2,
            repeat_count=5,
            workflow_id=workflow_id,
        ),
    )


def _make_event(
    event_type: EventType = EventType.REGISTER,
    workflow_id: str = "wf-1",
    from_agent: str = "A",
    to_agent: str = "",
    timestamp: float = 1.0,
) -> Event:
    return Event(
        type=event_type,
        timestamp=timestamp,
        workflow_id=workflow_id,
        from_agent=from_agent,
        to_agent=to_agent,
    )


def run_store_conformance(store_factory: Callable) -> None:
    """Run the full conformance suite against any Store implementation."""
    _test_record_and_list_detections(store_factory)
    _test_record_event_and_retrieve(store_factory)
    _test_list_detections_empty(store_factory)
    _test_list_detections_by_type(store_factory)
    _test_stats(store_factory)
    _test_close_is_idempotent(store_factory)
    _test_list_detections_limit(store_factory)
    _test_list_detections_by_type_limit(store_factory)
    _test_livelock_roundtrip(store_factory)


def _test_record_and_list_detections(store_factory: Callable) -> None:
    """Record a detection and list it back by workflow_id."""
    store = store_factory()
    try:
        detection = _make_deadlock_detection("wf-test")
        store.record_detection(detection)

        results = store.list_detections("wf-test")
        assert len(results) == 1
        d = results[0]
        assert d.type == DetectionType.DEADLOCK
        assert d.severity == Severity.CRITICAL
        assert d.cycle is not None
        assert set(d.cycle.agents) == {"A", "B"}
        assert d.cycle.workflow_id == "wf-test"

        # Different workflow should return empty
        assert store.list_detections("wf-other") == []
    finally:
        store.close()


def _test_record_event_and_retrieve(store_factory: Callable) -> None:
    """Record events and retrieve them by workflow_id."""
    store = store_factory()
    try:
        e1 = _make_event(
            EventType.REGISTER, workflow_id="wf-1", from_agent="A", timestamp=1.0
        )
        e2 = _make_event(
            EventType.REGISTER, workflow_id="wf-1", from_agent="B", timestamp=2.0
        )
        e3 = _make_event(
            EventType.REGISTER, workflow_id="wf-2", from_agent="X", timestamp=3.0
        )
        store.record_event(e1)
        store.record_event(e2)
        store.record_event(e3)

        wf1_events = store.get_workflow_events("wf-1")
        assert len(wf1_events) == 2
        assert all(e.workflow_id == "wf-1" for e in wf1_events)

        wf2_events = store.get_workflow_events("wf-2")
        assert len(wf2_events) == 1
        assert wf2_events[0].from_agent == "X"
    finally:
        store.close()


def _test_list_detections_empty(store_factory: Callable) -> None:
    """Empty store returns empty list."""
    store = store_factory()
    try:
        assert store.list_detections("wf-nope") == []
        assert store.list_detections_by_type(DetectionType.DEADLOCK) == []
    finally:
        store.close()


def _test_list_detections_by_type(store_factory: Callable) -> None:
    """list_detections_by_type filters correctly."""
    store = store_factory()
    try:
        dl = _make_deadlock_detection("wf-1")
        ll = _make_livelock_detection("wf-1")
        store.record_detection(dl)
        store.record_detection(ll)

        deadlocks = store.list_detections_by_type(DetectionType.DEADLOCK)
        assert len(deadlocks) == 1
        assert deadlocks[0].type == DetectionType.DEADLOCK

        livelocks = store.list_detections_by_type(DetectionType.LIVELOCK)
        assert len(livelocks) == 1
        assert livelocks[0].type == DetectionType.LIVELOCK
    finally:
        store.close()


def _test_stats(store_factory: Callable) -> None:
    """stats() reflects recorded data."""
    store = store_factory()
    try:
        # Initial state
        s = store.stats()
        assert s["total_detections"] == 0
        assert s["deadlocks_detected"] == 0
        assert s["livelocks_detected"] == 0
        assert s["total_events"] == 0

        # Add data
        store.record_detection(_make_deadlock_detection())
        store.record_detection(_make_livelock_detection())
        store.record_event(_make_event())
        store.record_event(_make_event(timestamp=2.0))

        s = store.stats()
        assert s["total_detections"] == 2
        assert s["deadlocks_detected"] == 1
        assert s["livelocks_detected"] == 1
        assert s["total_events"] == 2
    finally:
        store.close()


def _test_close_is_idempotent(store_factory: Callable) -> None:
    """Calling close() multiple times must not raise."""
    store = store_factory()
    store.close()
    store.close()  # Should not raise


def _test_list_detections_limit(store_factory: Callable) -> None:
    """list_detections respects the limit parameter."""
    store = store_factory()
    try:
        for _ in range(5):
            store.record_detection(_make_deadlock_detection("wf-limit"))

        results = store.list_detections("wf-limit", limit=3)
        assert len(results) == 3

        results_all = store.list_detections("wf-limit", limit=100)
        assert len(results_all) == 5
    finally:
        store.close()


def _test_list_detections_by_type_limit(store_factory: Callable) -> None:
    """list_detections_by_type respects the limit parameter."""
    store = store_factory()
    try:
        for _ in range(5):
            store.record_detection(_make_livelock_detection("wf-limit"))

        results = store.list_detections_by_type(DetectionType.LIVELOCK, limit=2)
        assert len(results) == 2

        results_all = store.list_detections_by_type(DetectionType.LIVELOCK, limit=100)
        assert len(results_all) == 5
    finally:
        store.close()


def _test_livelock_roundtrip(store_factory: Callable) -> None:
    """Livelock detection survives store round-trip with all fields intact."""
    store = store_factory()
    try:
        detection = _make_livelock_detection("wf-rt")
        store.record_detection(detection)

        results = store.list_detections("wf-rt")
        assert len(results) == 1
        d = results[0]
        assert d.type == DetectionType.LIVELOCK
        assert d.livelock is not None
        assert d.livelock.pattern_length == 2
        assert d.livelock.repeat_count == 5
        assert set(d.livelock.agents) == {"A", "B"}
        assert d.livelock.workflow_id == "wf-rt"
        assert d.livelock.resolved is False
    finally:
        store.close()
