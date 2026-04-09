# tests/examples/test_langgraph_deadlock_example.py
#
# Verifies that the LangGraph deadlock detection example pattern works correctly.
# Uses FakeClock for deterministic timing and disables periodic scans.

from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.graph import END, StateGraph

from tangle.config import TangleConfig
from tangle.integrations.langgraph import tangle_conditional_edge, tangle_node
from tangle.monitor import TangleMonitor
from tangle.types import DetectionType, Event, EventType
from tests.conftest import FakeClock


class WorkflowState(TypedDict):
    tangle_workflow_id: str
    request: str
    response: str
    iteration: int


@pytest.fixture()
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def monitor(fake_clock: FakeClock) -> TangleMonitor:
    config = TangleConfig(cycle_check_interval=999_999.0)
    return TangleMonitor(config=config, clock=fake_clock)


class TestDeadlockDetectionExample:
    """Tests verifying the LangGraph deadlock detection example scenario."""

    def test_deadlock_detected_via_direct_events(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """Classic 2-agent deadlock: agent_a waits for agent_b, agent_b waits for agent_a."""
        # Register both agents
        monitor.register(workflow_id="wf-example", agent_id="agent_a")
        fake_clock.advance(1)
        monitor.register(workflow_id="wf-example", agent_id="agent_b")
        fake_clock.advance(1)

        # agent_a waits for agent_b
        monitor.wait_for(
            workflow_id="wf-example",
            from_agent="agent_a",
            to_agent="agent_b",
            resource="data",
        )
        fake_clock.advance(1)

        # agent_b waits for agent_a — this closes the cycle
        detection = monitor.process_event(
            Event(
                type=EventType.WAIT_FOR,
                timestamp=fake_clock(),
                workflow_id="wf-example",
                from_agent="agent_b",
                to_agent="agent_a",
                resource="result",
            )
        )

        assert detection is not None, "Expected deadlock detection, got None"
        assert detection.type == DetectionType.DEADLOCK
        assert detection.cycle is not None
        assert set(detection.cycle.agents) == {"agent_a", "agent_b"}

    def test_stats_reflect_deadlock(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """stats() reports events_processed > 0 and active_detections > 0 after deadlock."""
        monitor.register(workflow_id="wf-stats", agent_id="agent_a")
        fake_clock.advance(1)
        monitor.register(workflow_id="wf-stats", agent_id="agent_b")
        fake_clock.advance(1)
        monitor.wait_for(
            workflow_id="wf-stats",
            from_agent="agent_a",
            to_agent="agent_b",
            resource="token",
        )
        fake_clock.advance(1)
        monitor.wait_for(
            workflow_id="wf-stats",
            from_agent="agent_b",
            to_agent="agent_a",
            resource="token",
        )

        s = monitor.stats()
        assert s["events_processed"] > 0
        assert s["active_detections"] > 0

    def test_active_detections_has_deadlock_type(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """active_detections() returns at least one DEADLOCK detection."""
        monitor.register(workflow_id="wf-active", agent_id="planner")
        fake_clock.advance(1)
        monitor.register(workflow_id="wf-active", agent_id="executor")
        fake_clock.advance(1)
        monitor.wait_for(
            workflow_id="wf-active",
            from_agent="planner",
            to_agent="executor",
            resource="plan",
        )
        fake_clock.advance(1)
        monitor.wait_for(
            workflow_id="wf-active",
            from_agent="executor",
            to_agent="planner",
            resource="result",
        )

        detections = monitor.active_detections()
        assert len(detections) > 0
        assert any(d.type == DetectionType.DEADLOCK for d in detections)

    def test_deadlock_via_langgraph_nodes_and_conditional_edges(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """Full LangGraph graph: two nodes with mutual conditional edges trigger deadlock."""

        # Build a graph where node_a routes to node_b and vice versa,
        # but we manually inject WAIT_FOR events to simulate the deadlock cycle.
        # The tangle_node decorator registers agents; tangle_conditional_edge
        # emits WAIT_FOR + RELEASE. To get a lasting deadlock we use the SDK
        # directly after registering via the decorator.

        @tangle_node(monitor, agent_id="orchestrator")
        def orchestrator_node(state: WorkflowState) -> dict:
            fake_clock.advance(1)
            return {"request": "process this"}

        @tangle_node(monitor, agent_id="worker")
        def worker_node(state: WorkflowState) -> dict:
            fake_clock.advance(1)
            return {"response": "done"}

        state: WorkflowState = {
            "tangle_workflow_id": "wf-lg-deadlock",
            "request": "",
            "response": "",
            "iteration": 0,
        }

        # Invoke both nodes to register them via the decorator
        orchestrator_node(state)
        worker_node(state)

        # Now manually create the deadlock: orchestrator waits for worker,
        # worker waits for orchestrator
        fake_clock.advance(1)
        monitor.wait_for(
            workflow_id="wf-lg-deadlock",
            from_agent="orchestrator",
            to_agent="worker",
            resource="approval",
        )
        fake_clock.advance(1)
        detection = monitor.process_event(
            Event(
                type=EventType.WAIT_FOR,
                timestamp=fake_clock(),
                workflow_id="wf-lg-deadlock",
                from_agent="worker",
                to_agent="orchestrator",
                resource="task",
            )
        )

        assert detection is not None
        assert detection.type == DetectionType.DEADLOCK
        assert detection.cycle is not None
        assert set(detection.cycle.agents) == {"orchestrator", "worker"}

        # Confirm stats
        s = monitor.stats()
        assert s["events_processed"] > 0
        assert s["active_detections"] > 0

    def test_langgraph_graph_invoke_registers_agents(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """A compiled StateGraph invocation registers agents via tangle_node decorators."""

        @tangle_node(monitor, agent_id="researcher")
        def researcher(state: WorkflowState) -> dict:
            fake_clock.advance(1)
            return {"request": "findings"}

        @tangle_node(monitor, agent_id="reviewer")
        def reviewer(state: WorkflowState) -> dict:
            fake_clock.advance(1)
            return {"response": "approved"}

        @tangle_conditional_edge(monitor, from_agent="reviewer")
        def route_after_review(state: WorkflowState) -> str:
            return END

        graph = StateGraph(WorkflowState)
        graph.add_node("researcher", researcher)
        graph.add_node("reviewer", reviewer)
        graph.set_entry_point("researcher")
        graph.add_edge("researcher", "reviewer")
        graph.add_conditional_edges("reviewer", route_after_review)

        app = graph.compile()
        app.invoke(
            {
                "tangle_workflow_id": "wf-lg-register",
                "request": "",
                "response": "",
                "iteration": 0,
            }
        )

        s = monitor.stats()
        assert s["events_processed"] > 0

        snap = monitor.snapshot("wf-lg-register")
        assert "researcher" in snap.nodes
        assert "reviewer" in snap.nodes

    def test_three_agent_deadlock_cycle(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """3-agent cycle: orchestrator -> planner -> executor -> orchestrator."""
        for agent in ("orchestrator", "planner", "executor"):
            monitor.register(workflow_id="wf-3cycle", agent_id=agent)
            fake_clock.advance(1)

        monitor.wait_for(
            workflow_id="wf-3cycle",
            from_agent="orchestrator",
            to_agent="planner",
            resource="plan",
        )
        fake_clock.advance(1)
        monitor.wait_for(
            workflow_id="wf-3cycle",
            from_agent="planner",
            to_agent="executor",
            resource="execution",
        )
        fake_clock.advance(1)

        detection = monitor.process_event(
            Event(
                type=EventType.WAIT_FOR,
                timestamp=fake_clock(),
                workflow_id="wf-3cycle",
                from_agent="executor",
                to_agent="orchestrator",
                resource="result",
            )
        )

        assert detection is not None
        assert detection.type == DetectionType.DEADLOCK
        assert detection.cycle is not None
        assert set(detection.cycle.agents) == {"orchestrator", "planner", "executor"}

        s = monitor.stats()
        assert s["events_processed"] > 0
        assert s["active_detections"] > 0
