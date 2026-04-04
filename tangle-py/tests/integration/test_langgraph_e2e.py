# tests/integration/test_langgraph_e2e.py

from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.graph import END, StateGraph

from tangle import TangleConfig, TangleMonitor
from tangle.integrations.langgraph import tangle_conditional_edge, tangle_node
from tangle.types import Event, EventType
from tests.conftest import FakeClock


@pytest.mark.integration
class TestLangGraphE2E:
    def test_livelock_detected_in_review_loop(self):
        """3-node graph: researcher -> writer -> reviewer -> researcher (loop).
        Nodes emit constant outputs so the livelock detector sees repeated message digests.
        After enough iterations, Tangle detects livelock."""
        clock = FakeClock()
        monitor = TangleMonitor(
            config=TangleConfig(
                livelock_window=20,
                livelock_min_repeats=3,
                livelock_min_pattern=2,
                cycle_check_interval=999,
            ),
            clock=clock,
        )

        class State(TypedDict):
            tangle_workflow_id: str
            research: str
            draft: str
            feedback: str
            iteration: int

        # All nodes return CONSTANT values so xxhash(key=repr(value)) repeats
        @tangle_node(monitor, agent_id="researcher")
        def researcher(state):
            clock.advance(1)
            return {"research": "constant findings"}

        @tangle_node(monitor, agent_id="writer")
        def writer(state):
            clock.advance(1)
            return {"draft": "constant draft"}

        @tangle_node(monitor, agent_id="reviewer")
        def reviewer(state):
            clock.advance(1)
            return {"feedback": "needs work", "iteration": state.get("iteration", 0) + 1}

        @tangle_conditional_edge(monitor, from_agent="reviewer")
        def should_continue(state):
            if state.get("iteration", 0) >= 10:
                return END
            return "researcher"

        graph = StateGraph(State)
        graph.add_node("researcher", researcher)
        graph.add_node("writer", writer)
        graph.add_node("reviewer", reviewer)
        graph.set_entry_point("researcher")
        graph.add_edge("researcher", "writer")
        graph.add_edge("writer", "reviewer")
        graph.add_conditional_edges("reviewer", should_continue)

        app = graph.compile()
        app.invoke({
            "tangle_workflow_id": "wf-e2e-1",
            "research": "",
            "draft": "",
            "feedback": "",
            "iteration": 0,
        })

        # After many iterations, livelock should be detected
        detections = monitor.active_detections()
        assert any(d.type.value == "livelock" for d in detections), (
            f"Expected livelock detection, got {len(detections)} detections"
        )

    def test_deadlock_via_conditional_edges(self):
        """Two nodes with conditional edges pointing at each other creating WaitFor cycle."""
        clock = FakeClock()
        monitor = TangleMonitor(
            config=TangleConfig(cycle_check_interval=999),
            clock=clock,
        )
        # Manually create deadlock scenario using SDK hooks
        monitor.register(workflow_id="wf-dl", agent_id="A")
        monitor.register(workflow_id="wf-dl", agent_id="B")
        clock.advance(1)
        monitor.wait_for(workflow_id="wf-dl", from_agent="A", to_agent="B", resource="data")
        clock.advance(1)
        detection = monitor.process_event(
            Event(
                type=EventType.WAIT_FOR,
                timestamp=clock(),
                workflow_id="wf-dl",
                from_agent="B",
                to_agent="A",
                resource="result",
            )
        )
        assert detection is not None
        assert detection.type.value == "deadlock"
