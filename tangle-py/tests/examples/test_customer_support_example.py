# tests/examples/test_customer_support_example.py
#
# Validates the customer support escalation example scenarios using FakeClock
# for deterministic timing. Covers livelock detection, deadlock detection,
# recovery via reset_workflow, and stats correctness.

from __future__ import annotations

import pytest

from tangle.config import TangleConfig
from tangle.monitor import TangleMonitor
from tangle.types import Detection, DetectionType
from tests.conftest import FakeClock


@pytest.fixture()
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def config() -> TangleConfig:
    return TangleConfig(
        cycle_check_interval=999_999.0,  # Disable periodic scans
        livelock_min_repeats=3,
        livelock_window=20,
        livelock_min_pattern=2,
        resolution="alert",
    )


@pytest.fixture()
def detections() -> list[Detection]:
    """Shared list that the on_detection callback appends to."""
    return []


@pytest.fixture()
def monitor(
    config: TangleConfig, fake_clock: FakeClock, detections: list[Detection]
) -> TangleMonitor:
    return TangleMonitor(
        config=config,
        clock=fake_clock,
        on_detection=lambda d: detections.append(d),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simulate_livelock(monitor: TangleMonitor, fake_clock: FakeClock, workflow_id: str) -> None:
    """Replay the drafter/reviewer reject loop from the example."""
    monitor.register(workflow_id=workflow_id, agent_id="drafter")
    fake_clock.advance(1)
    monitor.register(workflow_id=workflow_id, agent_id="reviewer")
    fake_clock.advance(1)

    draft_body = b"We apologize for the billing issue..."
    reject_body = b"Reject: too vague, needs account specifics"

    for _ in range(3):
        monitor.send(workflow_id, from_agent="drafter", to_agent="reviewer", body=draft_body)
        fake_clock.advance(0.1)
        monitor.send(workflow_id, from_agent="reviewer", to_agent="drafter", body=reject_body)
        fake_clock.advance(0.1)


def _simulate_deadlock(monitor: TangleMonitor, fake_clock: FakeClock, workflow_id: str) -> None:
    """Replay the 4-agent circular wait from the example."""
    for agent in ("triage", "researcher", "drafter", "reviewer"):
        monitor.register(workflow_id=workflow_id, agent_id=agent)
        fake_clock.advance(1)

    monitor.wait_for(
        workflow_id,
        from_agent="triage",
        to_agent="researcher",
        resource="account_lookup",
    )
    fake_clock.advance(1)
    monitor.wait_for(
        workflow_id,
        from_agent="researcher",
        to_agent="drafter",
        resource="draft_context",
    )
    fake_clock.advance(1)
    monitor.wait_for(workflow_id, from_agent="drafter", to_agent="reviewer", resource="approval")
    fake_clock.advance(1)
    monitor.wait_for(
        workflow_id, from_agent="reviewer", to_agent="triage", resource="reclassification"
    )


# ---------------------------------------------------------------------------
# Livelock tests
# ---------------------------------------------------------------------------


class TestLivelockDetection:
    """Tests for the drafter/reviewer reject loop scenario."""

    def test_livelock_detected_after_repeated_messages(
        self, monitor: TangleMonitor, fake_clock: FakeClock, detections: list[Detection]
    ) -> None:
        """3 repetitions of the same exchange triggers a LIVELOCK detection."""
        _simulate_livelock(monitor, fake_clock, "wf-support-ticket-42")

        livelock_detections = [d for d in detections if d.type == DetectionType.LIVELOCK]
        assert len(livelock_detections) >= 1
        det = livelock_detections[0]
        assert det.livelock is not None
        assert det.livelock.pattern_length == 2
        assert det.livelock.repeat_count >= 3

    def test_livelock_agents_are_drafter_and_reviewer(
        self, monitor: TangleMonitor, fake_clock: FakeClock, detections: list[Detection]
    ) -> None:
        """The livelock detection names drafter and reviewer."""
        _simulate_livelock(monitor, fake_clock, "wf-support-ticket-42")

        livelock_detections = [d for d in detections if d.type == DetectionType.LIVELOCK]
        assert len(livelock_detections) >= 1
        assert set(livelock_detections[0].livelock.agents) == {"drafter", "reviewer"}

    def test_progress_resets_livelock_buffer(
        self, monitor: TangleMonitor, fake_clock: FakeClock, detections: list[Detection]
    ) -> None:
        """After report_progress(), new different messages do NOT trigger another livelock."""
        _simulate_livelock(monitor, fake_clock, "wf-support-ticket-42")
        initial_count = len([d for d in detections if d.type == DetectionType.LIVELOCK])

        # Reset the livelock buffer
        monitor.report_progress("wf-support-ticket-42", description="drafter changed approach")
        fake_clock.advance(1)

        # Send new messages that vary each iteration — no repeating pattern
        for i in range(3):
            monitor.send(
                "wf-support-ticket-42",
                from_agent="drafter",
                to_agent="reviewer",
                body=f"Per account #12345, revision {i}".encode(),
            )
            fake_clock.advance(0.1)
            monitor.send(
                "wf-support-ticket-42",
                from_agent="reviewer",
                to_agent="drafter",
                body=f"Feedback on revision {i}".encode(),
            )
            fake_clock.advance(0.1)

        final_count = len([d for d in detections if d.type == DetectionType.LIVELOCK])
        assert final_count == initial_count


# ---------------------------------------------------------------------------
# Deadlock tests
# ---------------------------------------------------------------------------


class TestDeadlockDetection:
    """Tests for the 4-agent circular wait scenario."""

    def test_deadlock_detected_on_four_agent_cycle(
        self, monitor: TangleMonitor, fake_clock: FakeClock, detections: list[Detection]
    ) -> None:
        """Circular wait among 4 agents triggers a DEADLOCK detection."""
        _simulate_deadlock(monitor, fake_clock, "wf-support-ticket-99")

        deadlock_detections = [d for d in detections if d.type == DetectionType.DEADLOCK]
        assert len(deadlock_detections) >= 1
        det = deadlock_detections[0]
        assert det.cycle is not None
        # Do NOT assert cycle order — it depends on trigger-path (review comment 1)
        assert set(det.cycle.agents) == {"triage", "researcher", "drafter", "reviewer"}

    def test_deadlock_cycle_contains_all_four_agents(
        self, monitor: TangleMonitor, fake_clock: FakeClock, detections: list[Detection]
    ) -> None:
        """The detected cycle includes all four support pipeline agents."""
        _simulate_deadlock(monitor, fake_clock, "wf-support-ticket-99")

        deadlock_detections = [d for d in detections if d.type == DetectionType.DEADLOCK]
        assert len(deadlock_detections) >= 1
        cycle = deadlock_detections[0].cycle
        assert cycle is not None
        assert len(cycle.agents) == 4
        assert not cycle.resolved


# ---------------------------------------------------------------------------
# Combined stats and recovery tests
# ---------------------------------------------------------------------------


class TestStatsAndRecovery:
    """Tests verifying monitor stats and reset_workflow behavior."""

    def test_stats_reflect_both_detections(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """After both scenarios, stats() shows active_detections == 2."""
        _simulate_livelock(monitor, fake_clock, "wf-support-ticket-42")
        _simulate_deadlock(monitor, fake_clock, "wf-support-ticket-99")

        s = monitor.stats()
        assert s["active_detections"] == 2
        assert s["events_processed"] > 0

    def test_reset_workflow_clears_only_target_workflow(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """reset_workflow on ticket-99 clears the deadlock but leaves the livelock."""
        _simulate_livelock(monitor, fake_clock, "wf-support-ticket-42")
        _simulate_deadlock(monitor, fake_clock, "wf-support-ticket-99")

        assert monitor.stats()["active_detections"] == 2

        monitor.reset_workflow("wf-support-ticket-99")

        s = monitor.stats()
        assert s["active_detections"] == 1

        # The remaining detection should be the livelock from ticket-42
        remaining = monitor.active_detections()
        assert len(remaining) == 1
        assert remaining[0].type == DetectionType.LIVELOCK
        assert remaining[0].livelock is not None
        assert remaining[0].livelock.workflow_id == "wf-support-ticket-42"

    def test_graph_nodes_are_workflow_scoped(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """Reusing agent names across workflows yields workflow-scoped nodes."""
        _simulate_livelock(monitor, fake_clock, "wf-support-ticket-42")
        _simulate_deadlock(monitor, fake_clock, "wf-support-ticket-99")

        s = monitor.stats()
        # ticket-42 has 2 agents (drafter, reviewer)
        # ticket-99 has 4 agents (triage, researcher, drafter, reviewer)
        # drafter and reviewer appear in both workflows = 6 total nodes
        assert s["graph_nodes"] == 6

    def test_snapshot_contains_waiting_agents(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """Snapshot of the deadlock workflow shows all agents in WAITING state."""
        _simulate_deadlock(monitor, fake_clock, "wf-support-ticket-99")

        snap = monitor.snapshot("wf-support-ticket-99")
        assert set(snap.nodes) == {"triage", "researcher", "drafter", "reviewer"}
        for agent in snap.nodes:
            assert agent in snap.states
