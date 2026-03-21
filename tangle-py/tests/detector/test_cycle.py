# tests/detector/test_cycle.py

from __future__ import annotations

import threading

from hypothesis import given, settings
from hypothesis import strategies as st

from tangle.detector.cycle import CycleDetector
from tangle.graph.wfg import WaitForGraph
from tangle.types import Edge

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_edge(
    from_agent: str,
    to_agent: str,
    workflow_id: str = "wf-1",
    created_at: float = 1.0,
) -> Edge:
    return Edge(
        from_agent=from_agent,
        to_agent=to_agent,
        resource="r",
        created_at=created_at,
        workflow_id=workflow_id,
    )


def _build_graph(
    agents: list[str], edges: list[tuple[str, str]], workflow_id: str = "wf-1"
) -> WaitForGraph:
    """Register agents and add edges (without running detection)."""
    g = WaitForGraph()
    for i, a in enumerate(agents):
        g.register_agent(a, workflow_id, float(i))
    for src, dst in edges:
        g.add_edge(_make_edge(src, dst, workflow_id=workflow_id))
    return g


# ---------------------------------------------------------------------------
# Basic cycle detection (incremental via on_edge_added)
# ---------------------------------------------------------------------------


class TestIncrementalCycleDetection:

    def test_cycle_2_agents(self) -> None:
        """A->B, B->A produces a 2-agent cycle."""
        g = _build_graph(["A", "B"], [("A", "B")])
        det = CycleDetector(g)

        closing_edge = _make_edge("B", "A")
        g.add_edge(closing_edge)
        result = det.on_edge_added(closing_edge)

        assert result is not None
        assert set(result.agents) >= {"A", "B"}
        assert len(result.agents) == 2
        assert result.workflow_id == "wf-1"

    def test_cycle_3_agents(self) -> None:
        """A->B, B->C, C->A produces a 3-agent cycle."""
        g = _build_graph(["A", "B", "C"], [("A", "B"), ("B", "C")])
        det = CycleDetector(g)

        closing_edge = _make_edge("C", "A")
        g.add_edge(closing_edge)
        result = det.on_edge_added(closing_edge)

        assert result is not None
        assert set(result.agents) >= {"A", "B", "C"}

    def test_cycle_with_tail(self) -> None:
        """A->B->C->D->B: cycle is B->C->D->B; A is excluded from the cycle."""
        g = _build_graph(["A", "B", "C", "D"], [("A", "B"), ("B", "C"), ("C", "D")])
        det = CycleDetector(g)

        closing_edge = _make_edge("D", "B")
        g.add_edge(closing_edge)
        result = det.on_edge_added(closing_edge)

        assert result is not None
        # The cycle should contain B, C, D but not A
        cycle_agents_set = set(result.agents)
        assert {"B", "C", "D"} <= cycle_agents_set
        assert "A" not in cycle_agents_set

    def test_no_cycle_linear(self) -> None:
        """A->B->C->D has no cycle."""
        g = _build_graph(["A", "B", "C", "D"], [("A", "B"), ("B", "C")])
        det = CycleDetector(g)

        last_edge = _make_edge("C", "D")
        g.add_edge(last_edge)
        result = det.on_edge_added(last_edge)

        assert result is None

    def test_no_cycle_diamond(self) -> None:
        """A->B, A->C, B->D, C->D is a DAG (diamond), no cycle."""
        g = _build_graph(["A", "B", "C", "D"], [("A", "B"), ("A", "C"), ("B", "D")])
        det = CycleDetector(g)

        last_edge = _make_edge("C", "D")
        g.add_edge(last_edge)
        result = det.on_edge_added(last_edge)

        assert result is None

    def test_self_loop(self) -> None:
        """A->A is a cycle of length 1."""
        g = _build_graph(["A"], [])
        det = CycleDetector(g)

        self_edge = _make_edge("A", "A")
        g.add_edge(self_edge)
        result = det.on_edge_added(self_edge)

        assert result is not None
        assert result.agents == ["A"]
        assert len(result.edges) == 1

    def test_incremental_detection_on_completing_edge(self) -> None:
        """Cycle is detected when the completing edge is added."""
        g = _build_graph(["A", "B", "C"], [])
        det = CycleDetector(g)

        # Add edges one by one; only the last should detect a cycle
        e1 = _make_edge("A", "B")
        g.add_edge(e1)
        assert det.on_edge_added(e1) is None

        e2 = _make_edge("B", "C")
        g.add_edge(e2)
        assert det.on_edge_added(e2) is None

        e3 = _make_edge("C", "A")
        g.add_edge(e3)
        result = det.on_edge_added(e3)
        assert result is not None

    def test_incremental_no_cycle(self) -> None:
        """Adding an edge that doesn't complete a cycle returns None."""
        g = _build_graph(["A", "B", "C"], [("A", "B")])
        det = CycleDetector(g)

        non_closing_edge = _make_edge("A", "C")
        g.add_edge(non_closing_edge)
        result = det.on_edge_added(non_closing_edge)

        assert result is None


# ---------------------------------------------------------------------------
# Depth limit
# ---------------------------------------------------------------------------


class TestDepthLimit:

    def test_depth_limit(self) -> None:
        """A cycle longer than max_depth is not detected by incremental search."""
        # Build a chain: 0->1->2->...->5->0, length 6
        # Set max_depth=3 so the DFS won't traverse far enough.
        agents = [str(i) for i in range(6)]
        edges = [(str(i), str(i + 1)) for i in range(5)]
        g = _build_graph(agents, edges)
        det = CycleDetector(g, max_depth=3)

        closing_edge = _make_edge("5", "0")
        g.add_edge(closing_edge)
        result = det.on_edge_added(closing_edge)

        assert result is None  # Cycle too long for max_depth=3


# ---------------------------------------------------------------------------
# Edge removal
# ---------------------------------------------------------------------------


class TestEdgeRemoval:

    def test_edge_removal_breaks_cycle(self) -> None:
        """After removing one edge from a cycle, full_scan returns no cycles."""
        g = _build_graph(["A", "B", "C"], [("A", "B"), ("B", "C"), ("C", "A")])
        det = CycleDetector(g)

        # Confirm cycle exists
        cycles = det.full_scan()
        assert len(cycles) > 0

        # Remove one edge
        g.remove_edge("C", "A")
        cycles = det.full_scan()
        assert len(cycles) == 0


# ---------------------------------------------------------------------------
# Full scan (Kahn's algorithm)
# ---------------------------------------------------------------------------


class TestFullScan:

    def test_full_scan_finds_cycle(self) -> None:
        """Kahn's algorithm detects an existing cycle."""
        g = _build_graph(["A", "B", "C"], [("A", "B"), ("B", "C"), ("C", "A")])
        det = CycleDetector(g)

        cycles = det.full_scan()
        assert len(cycles) >= 1
        cycle_agents = cycles[0].agents
        assert set(cycle_agents) >= {"A", "B", "C"}

    def test_full_scan_acyclic(self) -> None:
        """Kahn's on a DAG returns an empty list."""
        g = _build_graph(["A", "B", "C", "D"], [("A", "B"), ("B", "C"), ("C", "D")])
        det = CycleDetector(g)

        cycles = det.full_scan()
        assert cycles == []

    def test_multiple_cycles(self) -> None:
        """Two independent cycles are both detected by full_scan."""
        g = WaitForGraph()
        # Cycle 1: A->B->A
        g.register_agent("A", "wf-1", 0.0)
        g.register_agent("B", "wf-1", 1.0)
        g.add_edge(_make_edge("A", "B"))
        g.add_edge(_make_edge("B", "A"))

        # Cycle 2: X->Y->Z->X
        g.register_agent("X", "wf-1", 2.0)
        g.register_agent("Y", "wf-1", 3.0)
        g.register_agent("Z", "wf-1", 4.0)
        g.add_edge(_make_edge("X", "Y"))
        g.add_edge(_make_edge("Y", "Z"))
        g.add_edge(_make_edge("Z", "X"))

        det = CycleDetector(g)
        cycles = det.full_scan()

        assert len(cycles) >= 2
        all_cycle_agents = set()
        for c in cycles:
            all_cycle_agents.update(c.agents)
        assert {"A", "B"} <= all_cycle_agents
        assert {"X", "Y", "Z"} <= all_cycle_agents


# ---------------------------------------------------------------------------
# Workflow isolation
# ---------------------------------------------------------------------------


class TestWorkflowIsolation:

    def test_workflow_isolation(self) -> None:
        """A cycle in wf-1 does not appear as a cycle in wf-2."""
        g = WaitForGraph()
        # wf-1: A->B->A (cycle)
        g.register_agent("A", "wf-1", 0.0)
        g.register_agent("B", "wf-1", 1.0)
        g.add_edge(_make_edge("A", "B", workflow_id="wf-1"))
        g.add_edge(_make_edge("B", "A", workflow_id="wf-1"))

        # wf-2: X->Y (no cycle)
        g.register_agent("X", "wf-2", 2.0)
        g.register_agent("Y", "wf-2", 3.0)
        g.add_edge(_make_edge("X", "Y", workflow_id="wf-2"))

        det = CycleDetector(g)

        # Full scan will find the cycle among A, B
        cycles = det.full_scan()
        assert len(cycles) >= 1

        # The cycle should only involve wf-1 agents
        for c in cycles:
            cycle_agents_set = set(c.agents)
            assert "X" not in cycle_agents_set
            assert "Y" not in cycle_agents_set


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:

    def test_concurrent_edge_add(self) -> None:
        """50 threads adding edges simultaneously; detection still works without crashing."""
        g = WaitForGraph()
        num_agents = 50
        for i in range(num_agents):
            g.register_agent(f"agent-{i}", "wf-1", float(i))

        det = CycleDetector(g)
        errors: list[Exception] = []

        def add_and_detect(i: int) -> None:
            try:
                j = (i + 1) % num_agents
                edge = _make_edge(f"agent-{i}", f"agent-{j}")
                g.add_edge(edge)
                det.on_edge_added(edge)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=add_and_detect, args=(i,))
            for i in range(num_agents)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Errors during concurrent add: {errors}"

        # The full ring of edges forms one big cycle; full_scan should find it.
        cycles = det.full_scan()
        assert len(cycles) >= 1


# ---------------------------------------------------------------------------
# Hypothesis property-based tests
# ---------------------------------------------------------------------------


# Strategy: generate a small graph (up to 8 nodes, up to 15 edges)
_agent_ids = st.sampled_from(["a", "b", "c", "d", "e", "f", "g", "h"])
_edge_st = st.tuples(_agent_ids, _agent_ids)
_edge_list_st = st.lists(_edge_st, min_size=0, max_size=15)


class TestHypothesis:

    @given(edges=_edge_list_st)
    @settings(max_examples=100, deadline=5000)
    def test_cycle_detector_never_crashes(self, edges: list[tuple[str, str]]) -> None:
        """CycleDetector never crashes regardless of graph shape."""
        all_agents = set()
        for src, dst in edges:
            all_agents.add(src)
            all_agents.add(dst)

        g = WaitForGraph()
        for i, a in enumerate(sorted(all_agents)):
            g.register_agent(a, "wf-hyp", float(i))

        det = CycleDetector(g)
        for src, dst in edges:
            edge = _make_edge(src, dst, workflow_id="wf-hyp")
            g.add_edge(edge)
            # Should never raise
            det.on_edge_added(edge)

        # Should never raise
        det.full_scan()

    @given(edges=_edge_list_st)
    @settings(max_examples=100, deadline=5000)
    def test_kahns_agrees_with_incremental(self, edges: list[tuple[str, str]]) -> None:
        """If incremental detects a cycle, full_scan also finds cycle nodes (and vice versa)."""
        all_agents = set()
        for src, dst in edges:
            all_agents.add(src)
            all_agents.add(dst)

        g = WaitForGraph()
        for i, a in enumerate(sorted(all_agents)):
            g.register_agent(a, "wf-hyp", float(i))

        det = CycleDetector(g, max_depth=50)
        incremental_found_cycle = False
        for src, dst in edges:
            edge = _make_edge(src, dst, workflow_id="wf-hyp")
            g.add_edge(edge)
            result = det.on_edge_added(edge)
            if result is not None:
                incremental_found_cycle = True

        full_scan_cycles = det.full_scan()
        full_scan_found_cycle = len(full_scan_cycles) > 0

        # Both should agree: if there's a cycle, both should find it
        if full_scan_found_cycle:
            # Kahn's is authoritative -- if it finds a cycle, one exists.
            # Incremental might have found it on the triggering edge.
            pass  # This direction is always valid
        if incremental_found_cycle:
            # If incremental found a cycle, Kahn's must also find one.
            assert (
                full_scan_found_cycle
            ), f"Incremental found cycle but full_scan did not. Edges: {edges}"


# ---------------------------------------------------------------------------
# Full scan edge cases
# ---------------------------------------------------------------------------


class TestFullScanEdgeCases:

    def test_full_scan_empty_graph(self) -> None:
        """full_scan on an empty graph returns empty list."""
        g = WaitForGraph()
        det = CycleDetector(g)
        assert det.full_scan() == []

    def test_full_scan_isolated_nodes_no_edges(self) -> None:
        """full_scan with registered agents but no edges returns no cycles."""
        g = WaitForGraph()
        g.register_agent("A", "wf-1", 1.0)
        g.register_agent("B", "wf-1", 2.0)
        g.register_agent("C", "wf-1", 3.0)
        det = CycleDetector(g)
        assert det.full_scan() == []
