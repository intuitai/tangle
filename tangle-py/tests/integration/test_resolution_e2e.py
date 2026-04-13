# tests/integration/test_resolution_e2e.py

from __future__ import annotations

import pytest

from tangle import TangleConfig, TangleMonitor
from tangle.types import Detection, ResolutionAction
from tests.conftest import FakeClock


def _raising_alert(detection: Detection) -> None:
    """on_detection callback that raises so AlertResolver fails and chain continues."""
    raise RuntimeError("alert-passthrough")


@pytest.mark.integration
class TestResolutionE2E:
    def test_cancel_youngest_on_deadlock(self):
        """Deadlock -> cancel_youngest resolver fires -> youngest agent canceled."""
        canceled_agents: list[tuple[str, str]] = []

        def cancel_fn(agent_id: str, reason: str) -> None:
            canceled_agents.append((agent_id, reason))

        clock = FakeClock()
        monitor = TangleMonitor(
            config=TangleConfig(
                resolution=ResolutionAction.CANCEL_YOUNGEST,
                cycle_check_interval=999,
            ),
            clock=clock,
            # AlertResolver must fail for CancelResolver to run
            on_detection=_raising_alert,
            cancel_fn=cancel_fn,
        )

        # Register agents at different times (B is younger)
        monitor.register(workflow_id="wf-res", agent_id="A")
        clock.advance(5)
        monitor.register(workflow_id="wf-res", agent_id="B")
        clock.advance(1)

        # Create deadlock
        monitor.wait_for(workflow_id="wf-res", from_agent="A", to_agent="B")
        clock.advance(1)
        monitor.wait_for(workflow_id="wf-res", from_agent="B", to_agent="A")

        # Verify cancel was called on youngest (B)
        assert len(canceled_agents) >= 1
        assert canceled_agents[0][0] == "B"
        assert "deadlock" in canceled_agents[0][1].lower()

    def test_cancel_all_on_deadlock(self):
        """Deadlock -> cancel_all resolver fires -> all agents canceled."""
        canceled_agents: list[str] = []

        def cancel_fn(agent_id: str, reason: str) -> None:
            canceled_agents.append(agent_id)

        clock = FakeClock()
        monitor = TangleMonitor(
            config=TangleConfig(
                resolution=ResolutionAction.CANCEL_ALL,
                cycle_check_interval=999,
            ),
            clock=clock,
            on_detection=_raising_alert,
            cancel_fn=cancel_fn,
        )

        monitor.register(workflow_id="wf-all", agent_id="A")
        clock.advance(1)
        monitor.register(workflow_id="wf-all", agent_id="B")
        clock.advance(1)
        monitor.wait_for(workflow_id="wf-all", from_agent="A", to_agent="B")
        clock.advance(1)
        monitor.wait_for(workflow_id="wf-all", from_agent="B", to_agent="A")

        assert set(canceled_agents) == {"A", "B"}

    def test_tiebreaker_on_livelock(self):
        """Livelock -> tiebreaker resolver fires -> prompt injected."""
        tiebreaker_calls: list[tuple[str, str]] = []

        def tiebreaker_fn(agent_id: str, prompt: str) -> None:
            tiebreaker_calls.append((agent_id, prompt))

        clock = FakeClock()
        monitor = TangleMonitor(
            config=TangleConfig(
                resolution=ResolutionAction.TIEBREAKER,
                livelock_window=20,
                livelock_min_repeats=3,
                livelock_min_pattern=2,
                cycle_check_interval=999,
            ),
            clock=clock,
            on_detection=_raising_alert,
            tiebreaker_fn=tiebreaker_fn,
        )

        monitor.register(workflow_id="wf-tb", agent_id="A")
        monitor.register(workflow_id="wf-tb", agent_id="B")

        # Create livelock: repeated ping-pong
        for _ in range(5):
            clock.advance(1)
            monitor.send(workflow_id="wf-tb", from_agent="A", to_agent="B", body=b"request")
            clock.advance(1)
            monitor.send(workflow_id="wf-tb", from_agent="B", to_agent="A", body=b"reject")

        assert len(tiebreaker_calls) >= 1
        # Default tiebreaker prompt contains "loop" or "stuck"
        prompt = tiebreaker_calls[0][1].lower()
        assert "loop" in prompt or "stuck" in prompt
