# tests/graph/test_wfg.py

from __future__ import annotations

import threading

from tangle.graph.wfg import WaitForGraph
from tangle.types import AgentStatus, Edge


def _edge(
    from_agent: str = "A",
    to_agent: str = "B",
    resource: str = "",
    created_at: float = 1.0,
    workflow_id: str = "wf-1",
) -> Edge:
    """Helper to build an Edge with sensible defaults."""
    return Edge(
        from_agent=from_agent,
        to_agent=to_agent,
        resource=resource,
        created_at=created_at,
        workflow_id=workflow_id,
    )


class TestAddEdge:
    def test_add_edge(self) -> None:
        g = WaitForGraph()
        g.add_edge(_edge("A", "B"))
        assert g.has_edge("A", "B")

    def test_add_edge_duplicate_idempotent(self) -> None:
        """Adding the same edge twice results in only one stored edge."""
        g = WaitForGraph()
        g.add_edge(_edge("A", "B"))
        g.add_edge(_edge("A", "B"))
        assert g.edge_count() == 1


class TestRemoveEdge:
    def test_remove_edge(self) -> None:
        g = WaitForGraph()
        g.add_edge(_edge("A", "B"))
        g.remove_edge("A", "B")
        assert not g.has_edge("A", "B")
        assert g.edge_count() == 0

    def test_remove_edge_not_found(self) -> None:
        """Removing a non-existent edge does not raise."""
        g = WaitForGraph()
        g.remove_edge("X", "Y")  # should not raise


class TestRegisterAgent:
    def test_register_agent(self) -> None:
        g = WaitForGraph()
        g.register_agent("A", "wf-1", 100.0)
        assert g.get_state("A") == AgentStatus.ACTIVE


class TestSetState:
    def test_set_state(self) -> None:
        """State transitions: ACTIVE -> WAITING -> COMPLETED."""
        g = WaitForGraph()
        g.register_agent("A", "wf-1", 100.0)
        assert g.get_state("A") == AgentStatus.ACTIVE

        g.set_state("A", AgentStatus.WAITING)
        assert g.get_state("A") == AgentStatus.WAITING

        g.set_state("A", AgentStatus.COMPLETED)
        assert g.get_state("A") == AgentStatus.COMPLETED


class TestHasEdge:
    def test_has_edge(self) -> None:
        g = WaitForGraph()
        g.add_edge(_edge("A", "B"))
        assert g.has_edge("A", "B") is True
        assert g.has_edge("B", "A") is False
        assert g.has_edge("A", "C") is False
        assert g.has_edge("X", "Y") is False


class TestOutgoing:
    def test_outgoing(self) -> None:
        g = WaitForGraph()
        g.add_edge(_edge("A", "B"))
        g.add_edge(_edge("A", "C"))
        g.add_edge(_edge("B", "C"))

        out_a = g.outgoing("A")
        assert len(out_a) == 2
        targets = {e.to_agent for e in out_a}
        assert targets == {"B", "C"}

        out_b = g.outgoing("B")
        assert len(out_b) == 1
        assert out_b[0].to_agent == "C"

        # No outgoing edges
        assert g.outgoing("C") == []
        assert g.outgoing("nonexistent") == []


class TestAllEdges:
    def test_all_edges(self) -> None:
        g = WaitForGraph()
        g.add_edge(_edge("A", "B"))
        g.add_edge(_edge("B", "C"))
        g.add_edge(_edge("C", "A"))
        edges = g.all_edges()
        assert len(edges) == 3
        pairs = {(e.from_agent, e.to_agent) for e in edges}
        assert pairs == {("A", "B"), ("B", "C"), ("C", "A")}


class TestAllNodes:
    def test_all_nodes(self) -> None:
        g = WaitForGraph()
        g.register_agent("A", "wf-1", 1.0)
        g.register_agent("B", "wf-1", 2.0)
        g.register_agent("C", "wf-1", 3.0)
        nodes = g.all_nodes()
        assert set(nodes) == {"A", "B", "C"}


class TestGetJoinTime:
    def test_get_join_time(self) -> None:
        g = WaitForGraph()
        g.register_agent("A", "wf-1", 42.5)
        assert g.get_join_time("A") == 42.5

    def test_get_join_time_unknown(self) -> None:
        g = WaitForGraph()
        assert g.get_join_time("unknown") is None


class TestEdgeCount:
    def test_edge_count(self) -> None:
        g = WaitForGraph()
        assert g.edge_count() == 0

        g.add_edge(_edge("A", "B"))
        assert g.edge_count() == 1

        g.add_edge(_edge("B", "C"))
        assert g.edge_count() == 2

        g.remove_edge("A", "B")
        assert g.edge_count() == 1

        g.remove_edge("B", "C")
        assert g.edge_count() == 0


class TestNodeCount:
    def test_node_count(self) -> None:
        g = WaitForGraph()
        assert g.node_count() == 0

        g.register_agent("A", "wf-1", 1.0)
        assert g.node_count() == 1

        g.register_agent("B", "wf-1", 2.0)
        assert g.node_count() == 2


class TestAgentsInWorkflow:
    def test_agents_in_workflow(self) -> None:
        g = WaitForGraph()
        g.register_agent("A", "wf-1", 1.0)
        g.register_agent("B", "wf-1", 2.0)
        g.register_agent("C", "wf-2", 3.0)

        wf1_agents = g.agents_in_workflow("wf-1")
        assert set(wf1_agents) == {"A", "B"}

        wf2_agents = g.agents_in_workflow("wf-2")
        assert wf2_agents == ["C"]

        assert g.agents_in_workflow("wf-999") == []


class TestClearWorkflow:
    def test_clear_workflow(self) -> None:
        """Removes all nodes and edges for a workflow."""
        g = WaitForGraph()
        g.register_agent("A", "wf-1", 1.0)
        g.register_agent("B", "wf-1", 2.0)
        g.register_agent("C", "wf-2", 3.0)
        g.add_edge(_edge("A", "B", workflow_id="wf-1"))
        g.add_edge(_edge("C", "A", workflow_id="wf-2"))

        g.clear_workflow("wf-1")

        # wf-1 agents should be gone
        assert g.agents_in_workflow("wf-1") == []
        assert g.get_state("A") is None
        assert g.get_state("B") is None
        assert g.get_join_time("A") is None

        # wf-2 should be unaffected
        assert g.agents_in_workflow("wf-2") == ["C"]
        assert g.get_state("C") == AgentStatus.ACTIVE

        # Edge A->B should be gone
        assert not g.has_edge("A", "B")

        # Edge C->A: from_agent C is still in wf-2, but to_agent A was cleared
        # The clear_workflow removes edges pointing TO cleared agents
        assert not g.has_edge("C", "A")


class TestSnapshot:
    def test_snapshot_isolation(self) -> None:
        """Mutating graph after snapshot does not affect the snapshot."""
        g = WaitForGraph()
        g.register_agent("A", "wf-1", 1.0)
        g.register_agent("B", "wf-1", 2.0)
        g.add_edge(_edge("A", "B"))

        snap = g.snapshot()
        assert len(snap.nodes) == 2
        assert len(snap.edges) == 1

        # Mutate graph
        g.register_agent("C", "wf-1", 3.0)
        g.add_edge(_edge("B", "C"))

        # Snapshot should be unchanged
        assert len(snap.nodes) == 2
        assert len(snap.edges) == 1


class TestGetState:
    def test_get_state_unknown_agent(self) -> None:
        """get_state returns None for an unregistered agent."""
        g = WaitForGraph()
        assert g.get_state("nonexistent") is None

    def test_get_state_after_set(self) -> None:
        """get_state reflects the most recent set_state call."""
        g = WaitForGraph()
        g.register_agent("A", "wf-1", 1.0)
        g.set_state("A", AgentStatus.CANCELED)
        assert g.get_state("A") == AgentStatus.CANCELED


class TestAddEdgeUnregistered:
    def test_edge_between_unregistered_agents(self) -> None:
        """Edges can be added between agents not registered via register_agent."""
        g = WaitForGraph()
        g.add_edge(_edge("X", "Y"))
        assert g.has_edge("X", "Y")
        assert g.edge_count() == 1
        # Unregistered agents don't appear in all_nodes
        assert "X" not in g.all_nodes()
        assert "Y" not in g.all_nodes()
        assert g.node_count() == 0


class TestClearWorkflowCrossEdge:
    def test_clear_workflow_removes_inbound_edge_and_cleans_key(self) -> None:
        """clear_workflow removes edges pointing to cleared agents and cleans empty dicts."""
        g = WaitForGraph()
        g.register_agent("A", "wf-1", 1.0)
        g.register_agent("X", "wf-2", 2.0)
        g.add_edge(_edge("X", "A", workflow_id="wf-2"))

        g.clear_workflow("wf-1")

        # Edge X->A removed (A was cleared)
        assert not g.has_edge("X", "A")
        # X has no outgoing edges left, so _edges["X"] dict should be cleaned up
        assert g.edge_count() == 0


class TestConcurrency:
    def test_concurrent_add_remove(self) -> None:
        """50 threads adding/removing edges concurrently -- no races."""
        g = WaitForGraph()
        num_threads = 50
        barrier = threading.Barrier(num_threads)
        errors: list[Exception] = []

        def worker(thread_id: int) -> None:
            try:
                barrier.wait(timeout=5)
                agent_from = f"agent_{thread_id}"
                agent_to = f"agent_{(thread_id + 1) % num_threads}"
                edge = _edge(agent_from, agent_to, created_at=float(thread_id))

                g.add_edge(edge)
                _ = g.has_edge(agent_from, agent_to)
                _ = g.edge_count()
                _ = g.all_edges()
                g.remove_edge(agent_from, agent_to)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Concurrent access produced errors: {errors}"
        # After all threads complete, all edges should be removed
        assert g.edge_count() == 0
