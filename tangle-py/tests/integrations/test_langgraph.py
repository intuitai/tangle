# tests/integrations/test_langgraph.py

from __future__ import annotations

from typing import Any

import pytest

from tangle.config import TangleConfig
from tangle.integrations.langgraph import tangle_conditional_edge, tangle_node
from tangle.monitor import TangleMonitor
from tangle.types import AgentStatus
from tests.conftest import FakeClock


@pytest.fixture()
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def monitor(fake_clock: FakeClock) -> TangleMonitor:
    config = TangleConfig(cycle_check_interval=999_999.0)
    return TangleMonitor(config=config, clock=fake_clock)


# ---------------------------------------------------------------------------
# tangle_node decorator
# ---------------------------------------------------------------------------


class TestTangleNode:
    def test_tangle_node_registers_agent(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """Decorated node emits a Register event on first call."""

        @tangle_node(monitor, "writer")
        def writer_node(state: dict[str, Any]) -> dict[str, Any]:
            return {"draft": "Hello"}

        state = {"tangle_workflow_id": "wf-1"}
        writer_node(state)

        snap = monitor.snapshot("wf-1")
        assert "writer" in snap.nodes
        assert snap.states["writer"] == AgentStatus.ACTIVE

    def test_tangle_node_registers_only_once(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """Second call in the same workflow does not re-register."""

        @tangle_node(monitor, "reader")
        def reader_node(state: dict[str, Any]) -> dict[str, Any]:
            return {}

        state = {"tangle_workflow_id": "wf-1"}
        reader_node(state)
        events_after_first = monitor.stats()["events_processed"]

        reader_node(state)
        events_after_second = monitor.stats()["events_processed"]

        # Second call should not add another REGISTER event
        # (only the return-value Send events, if any, would add)
        # The reader returns {}, so no Send events either.
        assert events_after_second == events_after_first

    def test_tangle_node_emits_send_for_changed_keys(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """Node returning {"draft": "..."} emits a Send event with resource="draft"."""

        @tangle_node(monitor, "drafter")
        def drafter_node(state: dict[str, Any]) -> dict[str, Any]:
            return {"draft": "Some content"}

        state = {"tangle_workflow_id": "wf-1"}
        drafter_node(state)

        # Should have: 1 REGISTER + 1 SEND (for "draft" key)
        assert monitor.stats()["events_processed"] == 2

    def test_tangle_node_emits_cancel_on_error(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """Exception in the wrapped function emits a Cancel event, then re-raises."""

        @tangle_node(monitor, "failing_agent")
        def failing_node(state: dict[str, Any]) -> dict[str, Any]:
            raise ValueError("something broke")

        state = {"tangle_workflow_id": "wf-1"}
        with pytest.raises(ValueError, match="something broke"):
            failing_node(state)

        snap = monitor.snapshot("wf-1")
        assert "failing_agent" in snap.nodes
        assert snap.states["failing_agent"] == AgentStatus.CANCELED

    def test_tangle_node_skips_tangle_keys(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """tangle_workflow_id key in result must NOT be emitted as a Send event."""

        @tangle_node(monitor, "meta_agent")
        def meta_node(state: dict[str, Any]) -> dict[str, Any]:
            return {"tangle_workflow_id": "wf-1", "output": "data"}

        state = {"tangle_workflow_id": "wf-1"}
        meta_node(state)

        # 1 REGISTER + 1 SEND (for "output"), NOT 2 SEND
        assert monitor.stats()["events_processed"] == 2

    def test_tangle_node_reregisters_after_reset_workflow(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """After reset_workflow(), calling the node again re-registers the agent."""

        @tangle_node(monitor, "resettable")
        def resettable_node(state: dict[str, Any]) -> dict[str, Any]:
            return {}

        state = {"tangle_workflow_id": "wf-reset"}
        resettable_node(state)

        # Agent should be registered
        snap = monitor.snapshot("wf-reset")
        assert "resettable" in snap.nodes

        events_after_first_run = monitor.stats()["events_processed"]

        # Reset the workflow — this clears the graph state
        monitor.reset_workflow("wf-reset")
        snap_after_reset = monitor.snapshot("wf-reset")
        assert "resettable" not in snap_after_reset.nodes

        # Call the node again — should re-register since the graph no longer knows about it
        resettable_node(state)
        events_after_second_run = monitor.stats()["events_processed"]

        # A new REGISTER event should have been emitted
        assert events_after_second_run > events_after_first_run
        snap_final = monitor.snapshot("wf-reset")
        assert "resettable" in snap_final.nodes


# ---------------------------------------------------------------------------
# tangle_conditional_edge decorator
# ---------------------------------------------------------------------------


class TestTangleConditionalEdge:
    def test_tangle_conditional_edge_emits_wait_and_release(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """Conditional edge emits a WaitFor followed by a Release."""

        @tangle_conditional_edge(monitor, "router")
        def route(state: dict[str, Any]) -> str:
            return "reviewer"

        # Pre-register the agents so the snapshot is populated
        monitor.register("wf-1", "router")
        monitor.register("wf-1", "reviewer")

        events_before = monitor.stats()["events_processed"]

        state = {"tangle_workflow_id": "wf-1"}
        result = route(state)

        assert result == "reviewer"

        # Should have emitted 2 events: WAIT_FOR + RELEASE
        assert monitor.stats()["events_processed"] == events_before + 2

        # RELEASE removes the edge, so snapshot should have no edges
        snap = monitor.snapshot("wf-1")
        wait_edges = [
            e
            for e in snap.edges
            if e.from_agent == "router" and e.to_agent == "reviewer"
        ]
        assert len(wait_edges) == 0

    def test_tangle_conditional_edge_skips_end(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """Edge returning "__end__" should not emit a WaitFor event."""

        @tangle_conditional_edge(monitor, "finalizer")
        def end_route(state: dict[str, Any]) -> str:
            return "__end__"

        monitor.register("wf-1", "finalizer")

        state = {"tangle_workflow_id": "wf-1"}
        result = end_route(state)

        assert result == "__end__"

        snap = monitor.snapshot("wf-1")
        # No edges should have been created
        assert len(snap.edges) == 0

    def test_tangle_conditional_edge_skips_empty_string(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """Edge returning empty string does not emit a WaitFor event."""

        @tangle_conditional_edge(monitor, "router")
        def empty_route(state: dict[str, Any]) -> str:
            return ""

        monitor.register("wf-1", "router")
        events_before = monitor.stats()["events_processed"]

        state = {"tangle_workflow_id": "wf-1"}
        result = empty_route(state)

        assert result == ""
        assert monitor.stats()["events_processed"] == events_before

    def test_tangle_conditional_edge_skips_none(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """Edge returning None does not emit a WaitFor event."""

        @tangle_conditional_edge(monitor, "router")
        def none_route(state: dict[str, Any]) -> str:
            return None  # type: ignore[return-value]

        monitor.register("wf-1", "router")
        events_before = monitor.stats()["events_processed"]

        state = {"tangle_workflow_id": "wf-1"}
        result = none_route(state)

        assert result is None
        assert monitor.stats()["events_processed"] == events_before

    def test_tangle_conditional_edge_default_workflow_id(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """When tangle_workflow_id is absent, 'default' is used."""

        @tangle_conditional_edge(monitor, "router")
        def route(state: dict[str, Any]) -> str:
            return "target"

        monitor.register("default", "router")
        monitor.register("default", "target")

        events_before = monitor.stats()["events_processed"]

        state = {}  # No tangle_workflow_id
        result = route(state)

        assert result == "target"
        # WAIT_FOR + RELEASE emitted
        assert monitor.stats()["events_processed"] == events_before + 2


# ---------------------------------------------------------------------------
# tangle_node edge cases
# ---------------------------------------------------------------------------


class TestTangleNodeEdgeCases:
    def test_tangle_node_default_workflow_id(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """When tangle_workflow_id is absent, 'default' is used."""

        @tangle_node(monitor, "agent")
        def my_node(state: dict[str, Any]) -> dict[str, Any]:
            return {"output": "data"}

        state = {}  # No tangle_workflow_id
        my_node(state)

        snap = monitor.snapshot("default")
        assert "agent" in snap.nodes

    def test_tangle_node_non_dict_return(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """Non-dict return skips Send events."""

        @tangle_node(monitor, "agent")
        def list_node(state: dict[str, Any]) -> list:
            return [1, 2, 3]

        state = {"tangle_workflow_id": "wf-1"}
        result = list_node(state)

        assert result == [1, 2, 3]
        # Only 1 REGISTER, no SEND events
        assert monitor.stats()["events_processed"] == 1

    def test_tangle_node_forwards_args_kwargs(
        self, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """Extra args and kwargs are forwarded to the wrapped function."""
        received: dict = {}

        @tangle_node(monitor, "agent")
        def node_with_args(
            state: dict[str, Any], extra: int, key: str = ""
        ) -> dict[str, Any]:
            received["extra"] = extra
            received["key"] = key
            return {}

        state = {"tangle_workflow_id": "wf-1"}
        node_with_args(state, 42, key="hello")

        assert received["extra"] == 42
        assert received["key"] == "hello"
