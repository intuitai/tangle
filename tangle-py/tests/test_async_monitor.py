# tests/test_async_monitor.py

from __future__ import annotations

import asyncio

from tangle.async_monitor import AsyncTangleMonitor
from tangle.config import TangleConfig
from tangle.types import Detection, DetectionType, Event, EventType
from tests.conftest import FakeClock, make_event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _feed(monitor: AsyncTangleMonitor, events: list[Event]) -> list[Detection]:
    """Process events through the async monitor, collecting any detections."""
    detections: list[Detection] = []
    for event in events:
        result = await monitor.process_event(event)
        if result is not None:
            detections.append(result)
    return detections


# ---------------------------------------------------------------------------
# Deadlock Detection
# ---------------------------------------------------------------------------


class TestAsyncDeadlockDetection:
    async def test_deadlock_detection(self, fake_clock: FakeClock, deadlock_2: list[Event]) -> None:
        """A->B, B->A should produce exactly one deadlock detection."""
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        detections = await _feed(monitor, deadlock_2)

        assert len(detections) == 1
        d = detections[0]
        assert d.type == DetectionType.DEADLOCK
        assert d.cycle is not None
        assert set(d.cycle.agents) >= {"A", "B"}
        assert d.cycle.workflow_id == "wf-1"
        assert d.cycle.resolved is False

    async def test_no_false_positive_linear(
        self, fake_clock: FakeClock, no_cycle_linear: list[Event]
    ) -> None:
        """A->B->C->D (no cycle) must not trigger any detection."""
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        detections = await _feed(monitor, no_cycle_linear)
        assert len(detections) == 0
        assert await monitor.active_detections() == []

    async def test_3_agent_cycle(self, fake_clock: FakeClock, deadlock_3: list[Event]) -> None:
        """A->B->C->A should produce exactly one deadlock detection."""
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        detections = await _feed(monitor, deadlock_3)

        assert len(detections) == 1
        d = detections[0]
        assert d.type == DetectionType.DEADLOCK
        assert d.cycle is not None
        assert set(d.cycle.agents) >= {"A", "B", "C"}


# ---------------------------------------------------------------------------
# Livelock Detection
# ---------------------------------------------------------------------------


class TestAsyncLivelockDetection:
    async def test_livelock_detection_pingpong(
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
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        detections = await _feed(monitor, livelock_pingpong)

        assert len(detections) >= 1
        livelock_detections = [d for d in detections if d.type == DetectionType.LIVELOCK]
        assert len(livelock_detections) >= 1
        d = livelock_detections[0]
        assert d.livelock is not None


# ---------------------------------------------------------------------------
# SDK hooks
# ---------------------------------------------------------------------------


class TestAsyncSDKHooks:
    async def test_wait_for_and_release(self, fake_clock: FakeClock) -> None:
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        await monitor.register("wf-1", "A")
        await monitor.register("wf-1", "B")
        await monitor.wait_for("wf-1", "A", "B")

        snap = await monitor.snapshot("wf-1")
        assert len(snap.edges) == 1

        await monitor.release("wf-1", "A", "B")
        snap = await monitor.snapshot("wf-1")
        assert len(snap.edges) == 0

    async def test_send_hook(self, fake_clock: FakeClock) -> None:
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        await monitor.register("wf-1", "A")
        await monitor.register("wf-1", "B")
        await monitor.send("wf-1", "A", "B", body=b"hello")

        s = await monitor.stats()
        assert s["events_processed"] == 3  # 2 registers + 1 send

    async def test_complete_removes_edges(self, fake_clock: FakeClock) -> None:
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        await monitor.register("wf-1", "A")
        await monitor.register("wf-1", "B")
        await monitor.wait_for("wf-1", "A", "B")
        await monitor.complete("wf-1", "B")

        snap = await monitor.snapshot("wf-1")
        assert len(snap.edges) == 0

    async def test_cancel_removes_edges(self, fake_clock: FakeClock) -> None:
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        await monitor.register("wf-1", "A")
        await monitor.register("wf-1", "B")
        await monitor.wait_for("wf-1", "A", "B")
        await monitor.cancel("wf-1", "A", reason="test")

        snap = await monitor.snapshot("wf-1")
        assert len(snap.edges) == 0

    async def test_report_progress(self, fake_clock: FakeClock) -> None:
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        await monitor.report_progress("wf-1", "step 1 done")
        s = await monitor.stats()
        assert s["events_processed"] == 1


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


class TestAsyncInspection:
    async def test_stats(self, fake_clock: FakeClock) -> None:
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        await monitor.register("wf-1", "A")
        await monitor.register("wf-1", "B")
        s = await monitor.stats()
        assert s["events_processed"] == 2
        assert s["graph_nodes"] == 2

    async def test_snapshot_all(self, fake_clock: FakeClock) -> None:
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        await monitor.register("wf-1", "A")
        await monitor.register("wf-1", "B")
        snap = await monitor.snapshot()
        assert len(snap.nodes) == 2

    async def test_snapshot_filtered(self, fake_clock: FakeClock) -> None:
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        await monitor.register("wf-1", "A")
        await monitor.register("wf-2", "B")
        snap = await monitor.snapshot("wf-1")
        assert set(snap.nodes) == {"A"}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestAsyncLifecycle:
    async def test_reset_workflow(self, fake_clock: FakeClock, deadlock_2: list[Event]) -> None:
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        await _feed(monitor, deadlock_2)
        assert len(await monitor.active_detections()) == 1

        await monitor.reset_workflow("wf-1")
        assert len(await monitor.active_detections()) == 0

        snap = await monitor.snapshot("wf-1")
        assert len(snap.edges) == 0
        assert len(snap.nodes) == 0

    async def test_context_manager(self, fake_clock: FakeClock) -> None:
        config = TangleConfig(cycle_check_interval=999_999.0)
        async with AsyncTangleMonitor(config=config, clock=fake_clock) as monitor:
            await monitor.register("wf-1", "A")
            s = await monitor.stats()
            assert s["events_processed"] == 1

    async def test_on_detection_callback(self, fake_clock: FakeClock) -> None:
        detections_received: list[Detection] = []
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(
            config=config, clock=fake_clock, on_detection=detections_received.append
        )

        await monitor.register("wf-1", "A")
        await monitor.register("wf-1", "B")
        await monitor.wait_for("wf-1", "A", "B")
        await monitor.wait_for("wf-1", "B", "A")

        assert len(detections_received) == 1
        assert detections_received[0].type == DetectionType.DEADLOCK


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestAsyncConcurrency:
    async def test_concurrent_event_processing(self, fake_clock: FakeClock) -> None:
        """Multiple coroutines processing events concurrently should not corrupt state."""
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        async def register_agents(workflow_id: str, agents: list[str]) -> None:
            for agent in agents:
                await monitor.register(workflow_id, agent)

        await asyncio.gather(
            register_agents("wf-1", [f"A{i}" for i in range(10)]),
            register_agents("wf-2", [f"B{i}" for i in range(10)]),
            register_agents("wf-3", [f"C{i}" for i in range(10)]),
        )

        s = await monitor.stats()
        assert s["events_processed"] == 30
        assert s["graph_nodes"] == 30

    async def test_concurrent_deadlock_detection(self, fake_clock: FakeClock) -> None:
        """Concurrent workflows can each independently detect deadlocks."""
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)

        async def create_deadlock(wf: str, a1: str, a2: str) -> list[Detection]:
            events = [
                make_event(EventType.REGISTER, workflow_id=wf, from_agent=a1, timestamp=1.0),
                make_event(EventType.REGISTER, workflow_id=wf, from_agent=a2, timestamp=2.0),
                make_event(
                    EventType.WAIT_FOR, workflow_id=wf, from_agent=a1, to_agent=a2, timestamp=3.0
                ),
                make_event(
                    EventType.WAIT_FOR, workflow_id=wf, from_agent=a2, to_agent=a1, timestamp=4.0
                ),
            ]
            return await _feed(monitor, events)

        results = await asyncio.gather(
            create_deadlock("wf-1", "A", "B"),
            create_deadlock("wf-2", "C", "D"),
        )

        total_deadlocks = sum(len(r) for r in results)
        assert total_deadlocks == 2

    async def test_lock_is_asyncio_lock(self, fake_clock: FakeClock) -> None:
        """Verify the monitor uses asyncio.Lock, not threading.Lock."""
        config = TangleConfig(cycle_check_interval=999_999.0)
        monitor = AsyncTangleMonitor(config=config, clock=fake_clock)
        assert isinstance(monitor._lock, asyncio.Lock)
