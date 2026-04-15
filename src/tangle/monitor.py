# src/tangle/monitor.py

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

    from tangle.metrics import TangleMetrics

from tangle.config import TangleConfig
from tangle.detector.cycle import CycleDetector
from tangle.detector.livelock import LivelockDetector
from tangle.graph.snapshot import GraphSnapshot
from tangle.graph.wfg import WaitForGraph
from tangle.logging import configure_logging, shutdown_logging
from tangle.resolver.alert import AlertResolver
from tangle.resolver.cancel import CancelResolver
from tangle.resolver.chain import ResolverChain
from tangle.resolver.errors import ResolutionExhaustedError
from tangle.resolver.escalate import EscalateResolver
from tangle.resolver.tiebreaker import TiebreakerResolver
from tangle.store.memory import MemoryStore
from tangle.store.sqlite import SQLiteStore
from tangle.types import (
    AgentID,
    AgentStatus,
    Detection,
    DetectionType,
    Edge,
    Event,
    EventType,
    Severity,
)

logger = structlog.get_logger("tangle")


class TangleMonitor:
    """Main entry point. Thread-safe."""

    def __init__(
        self,
        config: TangleConfig | None = None,
        clock: Callable[[], float] | None = None,
        on_detection: Callable[[Detection], None] | None = None,
        cancel_fn: Callable[[AgentID, str], None] | None = None,
        tiebreaker_fn: Callable[[AgentID, str], None] | None = None,
    ) -> None:
        self._config = config or TangleConfig()
        self._clock = clock or time.monotonic
        self._on_detection = on_detection

        configure_logging(
            otel_enabled=self._config.otel_enabled,
            otel_endpoint=self._config.otel_log_endpoint,
            service_name=self._config.service_name,
        )

        self._graph = WaitForGraph()
        self._cycle_detector = CycleDetector(self._graph, max_depth=self._config.max_cycle_length)
        self._livelock_detector = LivelockDetector(
            window=self._config.livelock_window,
            min_repeats=self._config.livelock_min_repeats,
            min_pattern=self._config.livelock_min_pattern,
            ring_size=self._config.livelock_ring_size,
        )

        self._detections: list[Detection] = []
        self._lock = threading.RLock()
        self._events_processed = 0

        # Store
        if self._config.store_backend == "sqlite":
            self._store = SQLiteStore(self._config.sqlite_path)
        else:
            self._store = MemoryStore()

        # Build resolver chain
        from tangle.types import ResolutionFailurePolicy

        raw_policy = self._config.resolution_failure_policy
        failure_policy = (
            ResolutionFailurePolicy(raw_policy) if isinstance(raw_policy, str) else raw_policy
        )
        self._resolver_chain = ResolverChain(
            failure_policy=failure_policy,
            max_attempts=self._config.max_resolution_attempts,
            retry_base_delay=self._config.resolution_retry_base_delay,
        )
        self._resolver_chain.add(AlertResolver(on_detection=on_detection))

        resolution = self._config.resolution
        if resolution in ("cancel_youngest", "cancel_all"):
            from tangle.types import ResolutionAction

            mode = (
                ResolutionAction.CANCEL_ALL
                if resolution == "cancel_all"
                else ResolutionAction.CANCEL_YOUNGEST
            )
            self._resolver_chain.add(CancelResolver(self._graph, cancel_fn=cancel_fn, mode=mode))
        if resolution == "tiebreaker":
            self._resolver_chain.add(
                TiebreakerResolver(
                    tiebreaker_fn=tiebreaker_fn,
                    prompt=self._config.tiebreaker_prompt,
                )
            )
        if resolution == "escalate":
            self._resolver_chain.add(
                EscalateResolver(
                    webhook_url=self._config.escalation_webhook_url,
                )
            )

        # Metrics
        self._metrics: TangleMetrics | None = None
        if self._config.metrics_enabled:
            from tangle.metrics import TangleMetrics as _TangleMetrics

            self._metrics = _TangleMetrics()

        # Background scan
        self._scan_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._otel_collector = None

    @property
    def metrics(self) -> TangleMetrics | None:
        return self._metrics

    def clock(self) -> float:
        return self._clock()

    # --- SDK hooks ---
    def wait_for(
        self,
        workflow_id: str,
        from_agent: AgentID,
        to_agent: AgentID,
        resource: str = "",
    ) -> None:
        self.process_event(
            Event(
                type=EventType.WAIT_FOR,
                timestamp=self._clock(),
                workflow_id=workflow_id,
                from_agent=from_agent,
                to_agent=to_agent,
                resource=resource,
            )
        )

    def release(self, workflow_id: str, from_agent: AgentID, to_agent: AgentID) -> None:
        self.process_event(
            Event(
                type=EventType.RELEASE,
                timestamp=self._clock(),
                workflow_id=workflow_id,
                from_agent=from_agent,
                to_agent=to_agent,
            )
        )

    def send(
        self,
        workflow_id: str,
        from_agent: AgentID,
        to_agent: AgentID,
        body: bytes = b"",
    ) -> None:
        self.process_event(
            Event(
                type=EventType.SEND,
                timestamp=self._clock(),
                workflow_id=workflow_id,
                from_agent=from_agent,
                to_agent=to_agent,
                message_body=body,
            )
        )

    def register(self, workflow_id: str, agent_id: AgentID) -> None:
        self.process_event(
            Event(
                type=EventType.REGISTER,
                timestamp=self._clock(),
                workflow_id=workflow_id,
                from_agent=agent_id,
            )
        )

    def complete(self, workflow_id: str, agent_id: AgentID) -> None:
        self.process_event(
            Event(
                type=EventType.COMPLETE,
                timestamp=self._clock(),
                workflow_id=workflow_id,
                from_agent=agent_id,
            )
        )

    def cancel(self, workflow_id: str, agent_id: AgentID, reason: str = "") -> None:
        self.process_event(
            Event(
                type=EventType.CANCEL,
                timestamp=self._clock(),
                workflow_id=workflow_id,
                from_agent=agent_id,
                resource=reason,
            )
        )

    def report_progress(self, workflow_id: str, description: str = "") -> None:
        self.process_event(
            Event(
                type=EventType.PROGRESS,
                timestamp=self._clock(),
                workflow_id=workflow_id,
                from_agent="__system__",
                resource=description,
            )
        )

    # --- Core ---
    def process_event(self, event: Event) -> Detection | None:
        with self._lock:
            self._events_processed += 1
            self._store.record_event(event)
            if self._metrics:
                self._metrics.record_event(event.type.value)

            detection: Detection | None = None

            if event.type == EventType.REGISTER:
                self._graph.register_agent(event.from_agent, event.workflow_id, event.timestamp)

            elif event.type == EventType.WAIT_FOR:
                edge = Edge(
                    from_agent=event.from_agent,
                    to_agent=event.to_agent,
                    resource=event.resource,
                    created_at=event.timestamp,
                    workflow_id=event.workflow_id,
                )
                self._graph.add_edge(edge)
                self._graph.set_state(
                    event.from_agent, AgentStatus.WAITING, workflow_id=event.workflow_id
                )
                cycle = self._cycle_detector.on_edge_added(edge)
                if cycle:
                    detection = Detection(
                        type=DetectionType.DEADLOCK,
                        severity=Severity.CRITICAL,
                        cycle=cycle,
                    )

            elif event.type == EventType.RELEASE:
                wf = event.workflow_id
                self._graph.remove_edge(event.from_agent, event.to_agent, workflow_id=wf)
                if self._graph.outgoing_count(event.from_agent, wf) == 0:
                    self._graph.set_state(event.from_agent, AgentStatus.ACTIVE, workflow_id=wf)

            elif event.type == EventType.SEND:
                pattern = self._livelock_detector.on_message(
                    from_agent=event.from_agent,
                    to_agent=event.to_agent,
                    body=event.message_body,
                    workflow_id=event.workflow_id,
                )
                if pattern:
                    detection = Detection(
                        type=DetectionType.LIVELOCK,
                        severity=Severity.CRITICAL,
                        livelock=pattern,
                    )

            elif event.type == EventType.COMPLETE:
                wf = event.workflow_id
                self._graph.set_state(event.from_agent, AgentStatus.COMPLETED, workflow_id=wf)
                # Remove all outgoing edges
                for edge in self._graph.outgoing(event.from_agent, workflow_id=wf):
                    self._graph.remove_edge(event.from_agent, edge.to_agent, workflow_id=wf)
                # Remove all inbound edges and unblock waiting agents
                sources = self._graph.remove_inbound(event.from_agent, wf)
                for src in sources:
                    if self._graph.outgoing_count(src, wf) == 0:
                        self._graph.set_state(src, AgentStatus.ACTIVE, workflow_id=wf)

            elif event.type == EventType.CANCEL:
                wf = event.workflow_id
                self._graph.set_state(event.from_agent, AgentStatus.CANCELED, workflow_id=wf)
                for edge in self._graph.outgoing(event.from_agent, workflow_id=wf):
                    self._graph.remove_edge(event.from_agent, edge.to_agent, workflow_id=wf)
                # Remove all inbound edges and unblock waiting agents
                sources = self._graph.remove_inbound(event.from_agent, wf)
                for src in sources:
                    if self._graph.outgoing_count(src, wf) == 0:
                        self._graph.set_state(src, AgentStatus.ACTIVE, workflow_id=wf)

            elif event.type == EventType.PROGRESS:
                self._livelock_detector.report_progress(event.workflow_id)

            if detection:
                self._detections.append(detection)
                self._store.record_detection(detection)
                if self._metrics:
                    self._metrics.record_detection(detection)
                try:
                    self._resolver_chain.resolve(detection)
                except ResolutionExhaustedError:
                    raise
                except Exception:
                    logger.exception("resolver_chain_failed")

            if self._metrics and event.type in (
                EventType.REGISTER,
                EventType.COMPLETE,
                EventType.CANCEL,
            ):
                self._metrics.set_active_workflows(self._graph.workflow_count())

            return detection

    # --- Inspection ---
    def snapshot(self, workflow_id: str | None = None) -> GraphSnapshot:
        with self._lock:
            if workflow_id:
                agents = self._graph.agents_in_workflow(workflow_id)
                all_edges = self._graph.all_edges()
                wf_edges = [e for e in all_edges if e.workflow_id == workflow_id]
                states = {}
                for a in agents:
                    s = self._graph.get_state(a, workflow_id=workflow_id)
                    if s:
                        states[a] = s
                return GraphSnapshot(nodes=agents, edges=wf_edges, states=states)
            return self._graph.snapshot()

    def active_detections(self) -> list[Detection]:
        with self._lock:
            return [
                d
                for d in self._detections
                if (d.cycle and not d.cycle.resolved) or (d.livelock and not d.livelock.resolved)
            ]

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "events_processed": self._events_processed,
                "active_detections": len(self.active_detections()),
                "graph_nodes": self._graph.node_count(),
                "graph_edges": self._graph.edge_count(),
            }

    # --- Lifecycle ---
    def start_background(self) -> None:
        if self._scan_thread is not None and self._scan_thread.is_alive():
            return
        self._stop_event.clear()
        self._scan_thread = threading.Thread(target=self._periodic_scan, daemon=True)
        self._scan_thread.start()
        if self._config.otel_enabled:
            from tangle.integrations.otel import OTelCollector

            self._otel_collector = OTelCollector(self, self._config.otel_port)
            self._otel_collector.start()

    def _periodic_scan(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(self._config.cycle_check_interval)
            if self._stop_event.is_set():
                break
            with self._lock:
                cycles = self._cycle_detector.full_scan()
                for cycle in cycles:
                    # Check if already detected — include workflow_id so two workflows
                    # with the same agent names don't suppress each other
                    already = any(
                        d.cycle
                        and d.cycle.workflow_id == cycle.workflow_id
                        and set(d.cycle.agents) == set(cycle.agents)
                        and not d.cycle.resolved
                        for d in self._detections
                    )
                    if not already:
                        detection = Detection(
                            type=DetectionType.DEADLOCK,
                            severity=Severity.CRITICAL,
                            cycle=cycle,
                        )
                        self._detections.append(detection)
                        self._store.record_detection(detection)
                        if self._metrics:
                            self._metrics.record_detection(detection)
                        try:
                            self._resolver_chain.resolve(detection)
                        except ResolutionExhaustedError:
                            logger.exception("periodic_resolution_exhausted")
                        except Exception:
                            logger.exception("periodic_resolver_failed")

    def stop(self) -> None:
        if self._otel_collector is not None:
            self._otel_collector.stop()
        self._stop_event.set()
        if self._scan_thread and self._scan_thread.is_alive():
            self._scan_thread.join(timeout=5)
        self._store.close()
        shutdown_logging()

    def reset_workflow(self, workflow_id: str) -> None:
        with self._lock:
            self._graph.clear_workflow(workflow_id)
            self._livelock_detector.clear_workflow(workflow_id)
            self._detections = [
                d
                for d in self._detections
                if not (
                    (d.cycle and d.cycle.workflow_id == workflow_id)
                    or (d.livelock and d.livelock.workflow_id == workflow_id)
                )
            ]

    def __enter__(self) -> TangleMonitor:
        self.start_background()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
