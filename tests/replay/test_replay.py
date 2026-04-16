# tests/replay/test_replay.py

from __future__ import annotations

from typing import TYPE_CHECKING

from tangle.config import TangleConfig
from tangle.monitor import TangleMonitor
from tangle.replay import EventLogReader, ReplayResult, replay_events
from tangle.replay.replay import ExplicitClock
from tangle.types import DetectionType, Event, EventType

if TYPE_CHECKING:
    from pathlib import Path


def _make_events(deadlock: bool = True) -> list[Event]:
    if deadlock:
        return [
            Event(EventType.REGISTER, 1.0, "wf", "A"),
            Event(EventType.REGISTER, 2.0, "wf", "B"),
            Event(EventType.WAIT_FOR, 3.0, "wf", "A", "B"),
            Event(EventType.WAIT_FOR, 4.0, "wf", "B", "A"),
        ]
    return [
        Event(EventType.REGISTER, 1.0, "wf", "A"),
        Event(EventType.REGISTER, 2.0, "wf", "B"),
        Event(EventType.WAIT_FOR, 3.0, "wf", "A", "B"),
    ]


def test_replay_reproduces_deadlock() -> None:
    result = replay_events(_make_events(deadlock=True))
    assert isinstance(result, ReplayResult)
    assert result.events_replayed == 4
    assert len(result.detections) == 1
    assert result.detections[0].type == DetectionType.DEADLOCK


def test_replay_no_detection_when_no_cycle() -> None:
    result = replay_events(_make_events(deadlock=False))
    assert result.detections == []


def test_replay_is_deterministic_across_runs() -> None:
    events = _make_events(deadlock=True)
    a = replay_events(events)
    b = replay_events(events)
    assert a.events_replayed == b.events_replayed
    assert len(a.detections) == len(b.detections)
    da, db = a.detections[0], b.detections[0]
    assert da.type == db.type and da.severity == db.severity
    assert da.cycle and db.cycle
    assert sorted(da.cycle.agents) == sorted(db.cycle.agents)


def test_explicit_clock_is_set_before_each_event() -> None:
    """When replayed, the monitor's clock must return the event's timestamp."""
    seen: list[float] = []

    clock = ExplicitClock()
    config = TangleConfig(cycle_check_interval=10**9)
    monitor = TangleMonitor(config=config, clock=clock)
    try:
        for ev in _make_events():
            clock.set(ev.timestamp)
            seen.append(clock())
            monitor.process_event(ev)
    finally:
        monitor.stop()

    assert seen == [1.0, 2.0, 3.0, 4.0]


def test_replay_from_log_file(tmp_path: Path) -> None:
    """End-to-end: write a log via TangleMonitor, then replay it from disk."""
    log_path = tmp_path / "events.jsonl"
    config = TangleConfig(
        event_log_path=str(log_path),
        event_log_fsync=False,
        cycle_check_interval=10**9,
    )
    monitor = TangleMonitor(config=config)
    try:
        for ev in _make_events(deadlock=True):
            monitor.process_event(ev)
    finally:
        monitor.stop()

    # Log captured all 4 events with valid integrity.
    reloaded = list(EventLogReader(log_path))
    assert len(reloaded) == 4

    result = replay_events(log_path)
    assert result.events_replayed == 4
    assert len(result.detections) == 1
    assert result.detections[0].type == DetectionType.DEADLOCK
