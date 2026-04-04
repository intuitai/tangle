# tests/test_monitor.py

from __future__ import annotations

import threading

import pytest

from tangle.config import TangleConfig
from tangle.monitor import TangleMonitor
from tangle.types import (
    AgentStatus,
    Detection,
    DetectionType,
    Event,
    EventType,
)
from tests.conftest import FakeClock, make_event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _feed(monitor: TangleMonitor, events: list[Event]) -> list[Detection]:
    """Process events through the monitor, collecting any detections."""
    detections: list[Detection] = []
    for event in events:
        result = monitor.process_event(event)
        if result is not None:
            detections.append(result)
    return detections


# ---------------------------------------------------------------------------
# Deadlock Detection
# ---------------------------------------------------------------------------


class TestDeadlockDetection:
    def test_deadlock_detection(
        self, monitor: TangleMonitor, deadlock_2: list[Event]
    ) -> None:
        """A->B, B->A should produce exactly one deadlock detection."""
        detections = _feed(monitor, deadlock_2)

        assert len(detections) == 1
        d = detections[0]
        assert d.type == DetectionType.DEADLOCK
        assert d.cycle is not None
        assert set(d.cycle.agents) >= {"A", "B"}
        assert d.cycle.workflow_id == "wf-1"
        assert d.cycle.resolved is False

    def test_no_false_positive_linear(
        self, monitor: TangleMonitor, no_cycle_linear: list[Event]
    ) -> None:
        """A->B->C->D (no cycle) must not trigger any detection."""
        detections = _feed(monitor, no_cycle_linear)
        assert len(detections) == 0
        assert monitor.active_detections() == []


# ---------------------------------------------------------------------------
# Livelock Detection
# ---------------------------------------------------------------------------


class TestLivelockDetection:
    def test_livelock_detection_pingpong(
        self, fake_clock: FakeClock, livelock_pingpong: list[Event]
    ) -> None:
        """Repeating request/reject ping-pong should trigger livelock detection."""
        config = TangleConfig(
            cycle_check_interval=999_999.0,
            livelock_window=50,
            livelock_min_repeats=3,
            livelock_min_pattern=2,
            livelock_ring_size=200,
        )
        monitor = TangleMonitor(config=config, clock=fake_clock)

        detections = _feed(monitor, livelock_pingpong)

        assert len(detections) >= 1
        livelock_detections = [
            d for d in detections if d.type == DetectionType.LIVELOCK
        ]
        assert len(livelock_detections) >= 1
        d = livelock_detections[0]
        assert d.livelock is not None
        assert d.livelock.pattern_length >= 1
        assert d.livelock.repeat_count >= 3
        assert d.livelock.workflow_id == "wf-1"

    def test_no_false_positive_progress(self, fake_clock: FakeClock) -> None:
        """Repetitive sends followed by a progress event should NOT trigger livelock."""
        config = TangleConfig(
            cycle_check_interval=999_999.0,
            livelock_window=50,
            livelock_min_repeats=3,
            livelock_min_pattern=2,
            livelock_ring_size=200,
        )
        monitor = TangleMonitor(config=config, clock=fake_clock)
        workflow_id = "wf-progress"

        # Register agents
        monitor.process_event(
            make_event(
                EventType.REGISTER,
                workflow_id=workflow_id,
                from_agent="A",
                timestamp=1.0,
            )
        )
        monitor.process_event(
            make_event(
                EventType.REGISTER,
                workflow_id=workflow_id,
                from_agent="B",
                timestamp=2.0,
            )
        )

        # Send a few repetitions (not enough to trigger with min_repeats=3,
        # min_pattern=2 -- need 3*2=6 messages, we send only 4)
        for i in range(2):
            t = 3.0 + i * 2
            monitor.process_event(
                make_event(
                    EventType.SEND,
                    workflow_id=workflow_id,
                    from_agent="A",
                    to_agent="B",
                    message_body=b"request",
                    timestamp=t,
                )
            )
            monitor.process_event(
                make_event(
                    EventType.SEND,
                    workflow_id=workflow_id,
                    from_agent="B",
                    to_agent="A",
                    message_body=b"reject",
                    timestamp=t + 1,
                )
            )

        # Progress event resets livelock counters
        monitor.report_progress(workflow_id, "made progress")

        # Send more repetitions after reset -- still not enough to trigger
        for i in range(2):
            t = 100.0 + i * 2
            monitor.process_event(
                make_event(
                    EventType.SEND,
                    workflow_id=workflow_id,
                    from_agent="A",
                    to_agent="B",
                    message_body=b"request",
                    timestamp=t,
                )
            )
            monitor.process_event(
                make_event(
                    EventType.SEND,
                    workflow_id=workflow_id,
                    from_agent="B",
                    to_agent="A",
                    message_body=b"reject",
                    timestamp=t + 1,
                )
            )

        # No livelock detections should have been raised
        livelock_detections = [
            d for d in monitor.active_detections() if d.type == DetectionType.LIVELOCK
        ]
        assert len(livelock_detections) == 0


# ---------------------------------------------------------------------------
# Edge Release
# ---------------------------------------------------------------------------


class TestEdgeRelease:
    def test_edge_release_breaks_cycle(self, monitor: TangleMonitor) -> None:
        """Adding a cycle then releasing one edge should clear the deadlock edges."""
        # Create cycle: A -> B -> A
        events = [
            make_event(EventType.REGISTER, from_agent="A", timestamp=1.0),
            make_event(EventType.REGISTER, from_agent="B", timestamp=2.0),
            make_event(EventType.WAIT_FOR, from_agent="A", to_agent="B", timestamp=3.0),
            make_event(EventType.WAIT_FOR, from_agent="B", to_agent="A", timestamp=4.0),
        ]
        detections = _feed(monitor, events)
        assert len(detections) == 1  # deadlock detected

        # Release A -> B
        monitor.process_event(
            make_event(
                EventType.RELEASE,
                from_agent="A",
                to_agent="B",
                timestamp=5.0,
            )
        )

        # Now the graph should have only B -> A, no cycle
        snap = monitor.snapshot("wf-1")
        assert len(snap.edges) == 1
        assert snap.edges[0].from_agent == "B"
        assert snap.edges[0].to_agent == "A"


# ---------------------------------------------------------------------------
# Workflow Reset
# ---------------------------------------------------------------------------


class TestWorkflowReset:
    def test_workflow_reset(
        self, monitor: TangleMonitor, deadlock_2: list[Event]
    ) -> None:
        """reset_workflow clears all state for that workflow."""
        _feed(monitor, deadlock_2)
        assert len(monitor.active_detections()) == 1

        monitor.reset_workflow("wf-1")

        assert len(monitor.active_detections()) == 0
        snap = monitor.snapshot("wf-1")
        assert snap.nodes == []
        assert snap.edges == []


# ---------------------------------------------------------------------------
# Multiple Workflows
# ---------------------------------------------------------------------------


class TestMultipleWorkflows:
    def test_multiple_workflows(self, monitor: TangleMonitor) -> None:
        """Deadlock in wf-1 should not affect wf-2."""
        # Deadlock in wf-1
        wf1_events = [
            make_event(
                EventType.REGISTER, workflow_id="wf-1", from_agent="A", timestamp=1.0
            ),
            make_event(
                EventType.REGISTER, workflow_id="wf-1", from_agent="B", timestamp=2.0
            ),
            make_event(
                EventType.WAIT_FOR,
                workflow_id="wf-1",
                from_agent="A",
                to_agent="B",
                timestamp=3.0,
            ),
            make_event(
                EventType.WAIT_FOR,
                workflow_id="wf-1",
                from_agent="B",
                to_agent="A",
                timestamp=4.0,
            ),
        ]
        # Normal activity in wf-2
        wf2_events = [
            make_event(
                EventType.REGISTER, workflow_id="wf-2", from_agent="X", timestamp=5.0
            ),
            make_event(
                EventType.REGISTER, workflow_id="wf-2", from_agent="Y", timestamp=6.0
            ),
            make_event(
                EventType.WAIT_FOR,
                workflow_id="wf-2",
                from_agent="X",
                to_agent="Y",
                timestamp=7.0,
            ),
        ]

        wf1_detections = _feed(monitor, wf1_events)
        wf2_detections = _feed(monitor, wf2_events)

        assert len(wf1_detections) == 1
        assert wf1_detections[0].type == DetectionType.DEADLOCK

        assert len(wf2_detections) == 0

        # wf-2 snapshot should have normal graph
        snap2 = monitor.snapshot("wf-2")
        assert len(snap2.nodes) == 2
        assert len(snap2.edges) == 1


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_snapshot(self, monitor: TangleMonitor) -> None:
        """Snapshot returns correct graph state."""
        events = [
            make_event(EventType.REGISTER, from_agent="A", timestamp=1.0),
            make_event(EventType.REGISTER, from_agent="B", timestamp=2.0),
            make_event(EventType.WAIT_FOR, from_agent="A", to_agent="B", timestamp=3.0),
        ]
        _feed(monitor, events)

        snap = monitor.snapshot("wf-1")
        assert set(snap.nodes) == {"A", "B"}
        assert len(snap.edges) == 1
        assert snap.edges[0].from_agent == "A"
        assert snap.edges[0].to_agent == "B"
        assert snap.states["A"] == AgentStatus.WAITING
        assert snap.states["B"] == AgentStatus.ACTIVE


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_stats(self, monitor: TangleMonitor, deadlock_2: list[Event]) -> None:
        """Stats reflect current state."""
        _feed(monitor, deadlock_2)

        stats = monitor.stats()
        assert stats["events_processed"] == 4
        assert stats["active_detections"] == 1
        assert stats["graph_nodes"] == 2
        assert stats["graph_edges"] == 2


# ---------------------------------------------------------------------------
# Context Manager
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_context_manager(self, fake_clock: FakeClock) -> None:
        """with monitor: starts and stops the background thread."""
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = TangleMonitor(config=config, clock=fake_clock)

        with monitor:
            assert monitor._scan_thread is not None
            assert monitor._scan_thread.is_alive()

        # After exiting, the thread should be stopped
        assert not monitor._scan_thread.is_alive()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_process_event(self, monitor: TangleMonitor) -> None:
        """50 threads sending events concurrently must not cause races."""
        barrier = threading.Barrier(50)
        errors: list[Exception] = []

        def worker(thread_id: int) -> None:
            try:
                barrier.wait(timeout=5)
                for i in range(20):
                    event = make_event(
                        EventType.SEND,
                        workflow_id="wf-concurrent",
                        from_agent=f"agent-{thread_id}",
                        to_agent=f"agent-{(thread_id + 1) % 50}",
                        message_body=f"msg-{i}".encode(),
                        timestamp=float(thread_id * 100 + i),
                    )
                    monitor.process_event(event)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Concurrent processing raised errors: {errors}"
        assert monitor.stats()["events_processed"] == 50 * 20


# ---------------------------------------------------------------------------
# SDK Hooks
# ---------------------------------------------------------------------------


class TestSDKHooks:
    def test_sdk_hooks_wait_for(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """wait_for convenience method creates the right event."""
        monitor.register("wf-sdk", "A")
        monitor.register("wf-sdk", "B")
        monitor.wait_for("wf-sdk", "A", "B", resource="data")

        snap = monitor.snapshot("wf-sdk")
        assert len(snap.edges) == 1
        assert snap.edges[0].from_agent == "A"
        assert snap.edges[0].to_agent == "B"
        assert snap.edges[0].resource == "data"

    def test_sdk_hooks_send(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """send convenience method works without errors."""
        monitor.register("wf-sdk", "A")
        monitor.register("wf-sdk", "B")
        monitor.send("wf-sdk", "A", "B", body=b"hello")

        assert monitor.stats()["events_processed"] == 3  # 2 register + 1 send

    def test_sdk_hooks_register(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """register convenience method registers the agent in the graph."""
        monitor.register("wf-sdk", "AgentX")

        snap = monitor.snapshot("wf-sdk")
        assert "AgentX" in snap.nodes
        assert snap.states["AgentX"] == AgentStatus.ACTIVE

    def test_sdk_hooks_complete(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """complete convenience method marks agent as completed and removes edges."""
        monitor.register("wf-sdk", "A")
        monitor.register("wf-sdk", "B")
        monitor.wait_for("wf-sdk", "A", "B")

        monitor.complete("wf-sdk", "A")

        snap = monitor.snapshot("wf-sdk")
        assert snap.states["A"] == AgentStatus.COMPLETED
        # Outgoing edges from A should be removed
        a_edges = [e for e in snap.edges if e.from_agent == "A"]
        assert len(a_edges) == 0

    def test_sdk_hooks_cancel(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """cancel convenience method marks agent as canceled and removes edges."""
        monitor.register("wf-sdk", "A")
        monitor.register("wf-sdk", "B")
        monitor.wait_for("wf-sdk", "A", "B")

        monitor.cancel("wf-sdk", "A", reason="timeout")

        snap = monitor.snapshot("wf-sdk")
        assert snap.states["A"] == AgentStatus.CANCELED
        a_edges = [e for e in snap.edges if e.from_agent == "A"]
        assert len(a_edges) == 0

    def test_sdk_hooks_release(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """release convenience method removes the edge and sets agent to ACTIVE."""
        monitor.register("wf-sdk", "A")
        monitor.register("wf-sdk", "B")
        monitor.wait_for("wf-sdk", "A", "B")

        monitor.release("wf-sdk", "A", "B")

        snap = monitor.snapshot("wf-sdk")
        assert len(snap.edges) == 0
        assert snap.states["A"] == AgentStatus.ACTIVE

    def test_sdk_hooks_report_progress(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """report_progress creates a PROGRESS event with from_agent=__system__."""
        monitor.report_progress("wf-sdk", "step completed")
        assert monitor.stats()["events_processed"] == 1


# ---------------------------------------------------------------------------
# Resolver wiring through monitor
# ---------------------------------------------------------------------------


class TestResolverWiring:
    def test_cancel_youngest_resolver_wiring(self, fake_clock: FakeClock) -> None:
        """Monitor with resolution=cancel_youngest calls cancel_fn on deadlock.

        AlertResolver is added first in the chain and stops on success.
        We force AlertResolver to fail (via a raising on_detection callback)
        so the chain falls through to CancelResolver.
        """
        canceled: list[tuple[str, str]] = []

        def cancel_fn(agent_id: str, reason: str) -> None:
            canceled.append((agent_id, reason))

        def bad_callback(det: Detection) -> None:
            raise RuntimeError("force fallthrough")

        config = TangleConfig(
            cycle_check_interval=999_999.0, resolution="cancel_youngest"
        )
        monitor = TangleMonitor(
            config=config,
            clock=fake_clock,
            cancel_fn=cancel_fn,
            on_detection=bad_callback,
        )

        monitor.register("wf-1", "A")
        fake_clock.advance(1)
        monitor.register("wf-1", "B")
        fake_clock.advance(1)
        monitor.wait_for("wf-1", "A", "B")
        monitor.wait_for("wf-1", "B", "A")  # triggers deadlock

        assert len(canceled) == 1
        assert canceled[0][0] == "B"  # B is younger
        assert "deadlock" in canceled[0][1]

    def test_cancel_all_resolver_wiring(self, fake_clock: FakeClock) -> None:
        """Monitor with resolution=cancel_all calls cancel_fn for all agents."""
        canceled_agents: list[str] = []

        def cancel_fn(agent_id: str, reason: str) -> None:
            canceled_agents.append(agent_id)

        def bad_callback(det: Detection) -> None:
            raise RuntimeError("force fallthrough")

        config = TangleConfig(cycle_check_interval=999_999.0, resolution="cancel_all")
        monitor = TangleMonitor(
            config=config,
            clock=fake_clock,
            cancel_fn=cancel_fn,
            on_detection=bad_callback,
        )

        monitor.register("wf-1", "A")
        monitor.register("wf-1", "B")
        monitor.wait_for("wf-1", "A", "B")
        monitor.wait_for("wf-1", "B", "A")

        assert set(canceled_agents) >= {"A", "B"}

    def test_tiebreaker_resolver_wiring(self, fake_clock: FakeClock) -> None:
        """Monitor with resolution=tiebreaker calls tiebreaker_fn on deadlock."""
        calls: list[tuple[str, str]] = []

        def tiebreaker_fn(agent_id: str, prompt: str) -> None:
            calls.append((agent_id, prompt))

        def bad_callback(det: Detection) -> None:
            raise RuntimeError("force fallthrough")

        config = TangleConfig(cycle_check_interval=999_999.0, resolution="tiebreaker")
        monitor = TangleMonitor(
            config=config,
            clock=fake_clock,
            tiebreaker_fn=tiebreaker_fn,
            on_detection=bad_callback,
        )

        monitor.register("wf-1", "A")
        monitor.register("wf-1", "B")
        monitor.wait_for("wf-1", "A", "B")
        monitor.wait_for("wf-1", "B", "A")

        assert len(calls) == 1
        assert "loop" in calls[0][1].lower() or "stuck" in calls[0][1].lower()

    def test_on_detection_callback(self, fake_clock: FakeClock) -> None:
        """on_detection callback receives the Detection when deadlock fires."""
        detections_received: list[Detection] = []

        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = TangleMonitor(
            config=config,
            clock=fake_clock,
            on_detection=detections_received.append,
        )

        monitor.register("wf-1", "A")
        monitor.register("wf-1", "B")
        monitor.wait_for("wf-1", "A", "B")
        monitor.wait_for("wf-1", "B", "A")

        assert len(detections_received) == 1
        assert detections_received[0].type == DetectionType.DEADLOCK


# ---------------------------------------------------------------------------
# Resolver exception handling
# ---------------------------------------------------------------------------


class TestResolverExceptionHandling:
    def test_failing_resolver_does_not_crash_process_event(
        self, fake_clock: FakeClock
    ) -> None:
        """A failing resolver logs but doesn't crash event processing."""
        config = TangleConfig(cycle_check_interval=999_999.0, resolution="tiebreaker")

        # No tiebreaker_fn provided + AlertResolver succeeds first, but let's
        # test with a deliberately broken on_detection callback
        def bad_callback(det: Detection) -> None:
            raise RuntimeError("callback boom")

        monitor = TangleMonitor(
            config=config,
            clock=fake_clock,
            on_detection=bad_callback,
        )

        monitor.register("wf-1", "A")
        monitor.register("wf-1", "B")
        # AlertResolver will call bad_callback, which raises, but chain stops on
        # first success. AlertResolver calls the callback AND succeeds (no raise).
        # Actually AlertResolver calls callback inside resolve, so if callback raises,
        # AlertResolver raises. Let's verify the monitor catches it.
        monitor.wait_for("wf-1", "A", "B")
        monitor.wait_for("wf-1", "B", "A")

        # Detection should still be recorded despite resolver failure
        assert len(monitor.active_detections()) == 1


# ---------------------------------------------------------------------------
# Periodic scan
# ---------------------------------------------------------------------------


class TestPeriodicScan:
    def test_periodic_scan_finds_cycle(self, fake_clock: FakeClock) -> None:
        """The periodic scan finds cycles that the incremental check may have missed."""
        config = TangleConfig(cycle_check_interval=0.05)
        monitor = TangleMonitor(config=config, clock=fake_clock)

        # Build a cycle that incremental detection already finds
        monitor.register("wf-1", "A")
        monitor.register("wf-1", "B")
        monitor.wait_for("wf-1", "A", "B")
        monitor.wait_for("wf-1", "B", "A")  # triggers incremental

        assert len(monitor.active_detections()) == 1

        # Start background, let scan run, it should NOT duplicate
        import time

        monitor.start_background()
        time.sleep(0.15)  # let at least one scan happen
        monitor.stop()

        # Should still be 1 -- the dedup guard prevents re-reporting
        assert len(monitor.active_detections()) == 1

    def test_periodic_scan_deduplication(self, fake_clock: FakeClock) -> None:
        """The periodic scan does not re-report an already-detected cycle."""
        config = TangleConfig(cycle_check_interval=0.05)
        monitor = TangleMonitor(config=config, clock=fake_clock)

        monitor.register("wf-1", "A")
        monitor.register("wf-1", "B")
        monitor.wait_for("wf-1", "A", "B")
        monitor.wait_for("wf-1", "B", "A")

        import time

        monitor.start_background()
        time.sleep(0.2)
        monitor.stop()

        deadlocks = [
            d for d in monitor.active_detections() if d.type == DetectionType.DEADLOCK
        ]
        assert len(deadlocks) == 1


# ---------------------------------------------------------------------------
# active_detections filtering
# ---------------------------------------------------------------------------


class TestActiveDetectionsFiltering:
    def test_resolved_detection_excluded(
        self, monitor: TangleMonitor, deadlock_2: list[Event]
    ) -> None:
        """active_detections excludes detections whose cycle is resolved."""
        _feed(monitor, deadlock_2)
        assert len(monitor.active_detections()) == 1

        # Mark the cycle as resolved
        monitor.active_detections()[0].cycle.resolved = True

        assert len(monitor.active_detections()) == 0


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


class TestSnapshotGlobal:
    def test_snapshot_without_workflow_id(self, monitor: TangleMonitor) -> None:
        """snapshot(None) returns the full global graph."""
        events = [
            make_event(
                EventType.REGISTER, workflow_id="wf-1", from_agent="A", timestamp=1.0
            ),
            make_event(
                EventType.REGISTER, workflow_id="wf-2", from_agent="X", timestamp=2.0
            ),
        ]
        _feed(monitor, events)

        snap = monitor.snapshot()
        assert "A" in snap.nodes
        assert "X" in snap.nodes


# ---------------------------------------------------------------------------
# Workflow reset with livelock
# ---------------------------------------------------------------------------


class TestWorkflowResetLivelock:
    def test_workflow_reset_clears_livelock(self, fake_clock: FakeClock) -> None:
        """reset_workflow clears livelock detector buffers."""
        config = TangleConfig(
            cycle_check_interval=999_999.0,
            livelock_window=50,
            livelock_min_repeats=3,
            livelock_min_pattern=2,
            livelock_ring_size=200,
        )
        monitor = TangleMonitor(config=config, clock=fake_clock)

        monitor.register("wf-ll", "A")
        monitor.register("wf-ll", "B")
        # Send enough to almost trigger livelock (2 repeats of pattern len 2 = 4 msgs)
        for _ in range(2):
            monitor.send("wf-ll", "A", "B", body=b"req")
            monitor.send("wf-ll", "B", "A", body=b"rej")

        # Reset workflow -- clears buffers
        monitor.reset_workflow("wf-ll")

        # Send same pattern again -- should not trigger because counters reset
        monitor.register("wf-ll", "A")
        monitor.register("wf-ll", "B")
        for _ in range(2):
            monitor.send("wf-ll", "A", "B", body=b"req")
            monitor.send("wf-ll", "B", "A", body=b"rej")

        livelock_dets = [
            d for d in monitor.active_detections() if d.type == DetectionType.LIVELOCK
        ]
        assert len(livelock_dets) == 0


# ---------------------------------------------------------------------------
# stop() without start_background()
# ---------------------------------------------------------------------------


class TestStopWithoutStart:
    def test_stop_without_start(self, monitor: TangleMonitor) -> None:
        """stop() is safe to call without a prior start_background()."""
        monitor.stop()  # Should not raise


# ---------------------------------------------------------------------------
# Periodic scan catches missed cycles (bypassing incremental detection)
# ---------------------------------------------------------------------------


class TestPeriodicScanCatchesMissed:
    def test_periodic_check_catches_missed(self, fake_clock: FakeClock) -> None:
        """Periodic scan catches a cycle injected directly into the graph,
        bypassing process_event so incremental detection never fires."""
        import time

        config = TangleConfig(cycle_check_interval=0.1)
        monitor = TangleMonitor(config=config, clock=fake_clock)

        # Inject edges directly into the graph, bypassing process_event
        from tangle.types import Edge

        edge_ab = Edge(
            from_agent="A",
            to_agent="B",
            resource="",
            created_at=1.0,
            workflow_id="wf-missed",
        )
        edge_ba = Edge(
            from_agent="B",
            to_agent="A",
            resource="",
            created_at=2.0,
            workflow_id="wf-missed",
        )
        monitor._graph.register_agent("A", "wf-missed", 1.0)
        monitor._graph.register_agent("B", "wf-missed", 2.0)
        monitor._graph.add_edge(edge_ab)
        monitor._graph.add_edge(edge_ba)

        # No detection yet — incremental path was bypassed
        assert len(monitor.active_detections()) == 0

        monitor.start_background()
        time.sleep(0.35)  # allow at least one periodic scan
        monitor.stop()

        assert len(monitor.active_detections()) >= 1
        assert monitor.active_detections()[0].type == DetectionType.DEADLOCK


# ---------------------------------------------------------------------------
# Resolution retry — MockResolver failure keeps detection active
# ---------------------------------------------------------------------------


class TestResolutionRetry:
    def test_resolution_retry(self, fake_clock: FakeClock) -> None:
        """When the only resolver fails, the detection stays in active_detections."""
        from tests.conftest import MockResolver

        config = TangleConfig(cycle_check_interval=999_999.0)
        mock = MockResolver()
        mock.should_fail = True

        monitor = TangleMonitor(config=config, clock=fake_clock)
        # Replace the resolver chain with our failing mock
        from tangle.resolver.chain import ResolverChain

        chain = ResolverChain()
        chain.add(mock)
        monitor._resolver_chain = chain

        monitor.register("wf-retry", "A")
        monitor.register("wf-retry", "B")
        monitor.wait_for("wf-retry", "A", "B")
        monitor.wait_for("wf-retry", "B", "A")  # triggers deadlock

        # Detection is recorded even though resolver failed
        assert len(monitor.active_detections()) == 1
        # MockResolver was called once
        assert mock.count == 1  # detection appended before resolver raised


# ---------------------------------------------------------------------------
# OTel collector lifecycle
# ---------------------------------------------------------------------------


class TestOTelCollector:
    @pytest.mark.integration
    def test_otel_collector_starts_when_enabled(self, fake_clock: FakeClock) -> None:
        """OTel collector starts a gRPC server when otel_enabled=True."""
        import grpc

        port = 14317  # high port unlikely to be in use
        config = TangleConfig(
            cycle_check_interval=999_999.0, otel_enabled=True, otel_port=port
        )
        monitor = TangleMonitor(config=config, clock=fake_clock)
        monitor.start_background()
        try:
            channel = grpc.insecure_channel(f"localhost:{port}")
            # Attempt a trivial connectivity check — the channel should be ready
            future = grpc.channel_ready_future(channel)
            future.result(timeout=3)
            channel.close()
        finally:
            monitor.stop()

    def test_otel_collector_skipped_when_disabled(self, fake_clock: FakeClock) -> None:
        """OTel collector is None when otel_enabled=False (default)."""
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = TangleMonitor(config=config, clock=fake_clock)
        monitor.start_background()
        try:
            assert monitor._otel_collector is None
        finally:
            monitor.stop()
