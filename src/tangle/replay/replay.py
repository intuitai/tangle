# src/tangle/replay/replay.py

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from tangle.config import TangleConfig
from tangle.monitor import TangleMonitor
from tangle.replay.log import EventLogReader

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tangle.types import Detection, Event


class ExplicitClock:
    """Clock whose value is advanced only by ``set()``.

    During replay, each recorded event's ``timestamp`` is set on the clock
    *before* the event is processed, so any code path inside the monitor that
    reads the clock (e.g. ``_join_times``) observes the same value it did
    during the original run.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def set(self, t: float) -> None:
        self._now = t


@dataclass(slots=True)
class ReplayResult:
    """Outcome of a replay run."""

    detections: list[Detection] = field(default_factory=list)
    events_replayed: int = 0
    final_stats: dict[str, int] = field(default_factory=dict)


def _load_events(source: str | Path | Iterable[Event]) -> Iterable[Event]:
    if isinstance(source, (str, Path)):
        return EventLogReader(source)
    return source


def replay_events(
    source: str | Path | Iterable[Event],
    config: TangleConfig | None = None,
) -> ReplayResult:
    """Replay events into a fresh monitor and return the detections produced.

    The returned monitor's background scan thread is never started. The clock
    is driven by recorded timestamps, so the replay is deterministic given the
    same code + events.
    """
    clock = ExplicitClock()
    # Disable periodic scans; replay must be purely event-driven.
    replay_config = (config or TangleConfig()).model_copy(
        update={"cycle_check_interval": 10**9, "otel_enabled": False, "metrics_enabled": False}
    )
    monitor = TangleMonitor(config=replay_config, clock=clock)
    detections: list[Detection] = []
    count = 0
    try:
        for event in _load_events(source):
            clock.set(event.timestamp)
            detection = monitor.process_event(event)
            if detection is not None:
                detections.append(detection)
            count += 1
        stats = monitor.stats()
    finally:
        monitor.stop()
    return ReplayResult(detections=detections, events_replayed=count, final_stats=stats)
