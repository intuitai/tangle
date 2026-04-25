# src/tangle/retention.py

"""Retention and eviction policy for completed workflows.

The monitor accumulates per-workflow state in three places: the wait-for graph,
the livelock detector's ring buffers, and the resolved-detection list. Without
eviction, long-running sidecars leak memory linearly with workflow count.

:class:`RetentionManager` tracks per-workflow last-activity timestamps and
sweeps eligible workflows on a periodic cadence. A workflow is eligible for
TTL eviction only when every registered agent has reached
:class:`AgentStatus.COMPLETED` or :class:`AgentStatus.CANCELED` — a stuck or
deadlocked workflow is never evicted by age, since detecting that state is
the monitor's whole purpose.

When :attr:`max_active_workflows` is exceeded the manager evicts terminal
workflows first (ignoring TTL); if none exist it records the overflow but
does not force-evict in-flight workflows.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from tangle.types import AgentStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from tangle.detector.livelock import LivelockDetector
    from tangle.graph.wfg import WaitForGraph
    from tangle.types import Detection, Event

logger = structlog.get_logger("tangle.retention")


@dataclass(slots=True)
class SweepResult:
    """Outcome of a single retention sweep."""

    evicted_ttl: int = 0
    evicted_capacity: int = 0
    overflow_unresolved: int = 0
    retained_workflows: int = 0


class RetentionManager:
    """Periodically prunes completed workflows from monitor state."""

    def __init__(
        self,
        graph: WaitForGraph,
        livelock_detector: LivelockDetector,
        clock: Callable[[], float],
        completed_ttl: float = 0.0,
        max_active_workflows: int = 0,
    ) -> None:
        self._graph = graph
        self._livelock = livelock_detector
        self._clock = clock
        self._completed_ttl = completed_ttl
        self._max_active = max_active_workflows
        self._lock = threading.Lock()
        self._last_activity: dict[str, float] = {}

    # --- Tracking ---

    def note_event(self, event: Event) -> None:
        """Record activity for a workflow. Called on every processed event."""
        if not event.workflow_id:
            return
        with self._lock:
            self._last_activity[event.workflow_id] = event.timestamp

    def forget_workflow(self, workflow_id: str) -> None:
        """Drop tracking for a workflow that was reset out-of-band."""
        with self._lock:
            self._last_activity.pop(workflow_id, None)

    def tracked_count(self) -> int:
        with self._lock:
            return len(self._last_activity)

    # --- Eviction ---

    def sweep(self, on_evict: Callable[[str], None]) -> SweepResult:
        """Evict expired and over-capacity workflows.

        ``on_evict`` is invoked once per evicted workflow_id with the monitor
        lock already held by the caller. The callback is responsible for
        clearing detections that reference the workflow; this manager
        clears the graph and livelock buffers itself.
        """
        result = SweepResult()
        now = self._clock()

        with self._lock:
            terminal: list[tuple[str, float]] = []
            non_terminal: list[tuple[str, float]] = []
            for wf_id, last in self._last_activity.items():
                if self._is_terminal(wf_id):
                    terminal.append((wf_id, last))
                else:
                    non_terminal.append((wf_id, last))

            evict: list[str] = []

            if self._completed_ttl > 0:
                cutoff = now - self._completed_ttl
                for wf_id, last in terminal:
                    if last <= cutoff:
                        evict.append(wf_id)
                        result.evicted_ttl += 1

            if self._max_active > 0:
                projected = len(self._last_activity) - len(evict)
                if projected > self._max_active:
                    overflow = projected - self._max_active
                    remaining_terminal = [(wf, last) for (wf, last) in terminal if wf not in evict]
                    remaining_terminal.sort(key=lambda x: x[1])
                    take = min(overflow, len(remaining_terminal))
                    for wf_id, _ in remaining_terminal[:take]:
                        evict.append(wf_id)
                        result.evicted_capacity += 1
                    still_over = overflow - take
                    if still_over > 0:
                        result.overflow_unresolved = still_over
                        logger.warning(
                            "retention_capacity_overflow",
                            over_by=still_over,
                            cap=self._max_active,
                            non_terminal=len(non_terminal),
                        )

            for wf_id in evict:
                self._last_activity.pop(wf_id, None)

            result.retained_workflows = len(self._last_activity)

        for wf_id in evict:
            self._graph.clear_workflow(wf_id)
            self._livelock.clear_workflow(wf_id)
            on_evict(wf_id)

        return result

    # --- Helpers ---

    def _is_terminal(self, workflow_id: str) -> bool:
        agents = self._graph.agents_in_workflow(workflow_id)
        if not agents:
            return True
        for agent in agents:
            state = self._graph.get_state(agent, workflow_id=workflow_id)
            if state not in (AgentStatus.COMPLETED, AgentStatus.CANCELED):
                return False
        return True


def detection_belongs_to(detection: Detection, workflow_id: str) -> bool:
    """True if a detection references the given workflow."""
    if detection.cycle and detection.cycle.workflow_id == workflow_id:
        return True
    return bool(detection.livelock and detection.livelock.workflow_id == workflow_id)
