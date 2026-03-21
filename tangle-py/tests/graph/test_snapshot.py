# tests/graph/test_snapshot.py

from __future__ import annotations

import json

from tangle.graph.snapshot import GraphSnapshot
from tangle.types import AgentStatus, Edge


def _snapshot_with_data() -> GraphSnapshot:
    """Build a snapshot with known data for testing."""
    edges = [
        Edge(
            from_agent="A",
            to_agent="B",
            resource="file.txt",
            created_at=1.0,
            workflow_id="wf-1",
        ),
        Edge(
            from_agent="B",
            to_agent="C",
            resource="",
            created_at=2.0,
            workflow_id="wf-1",
        ),
    ]
    states = {
        "A": AgentStatus.WAITING,
        "B": AgentStatus.WAITING,
        "C": AgentStatus.ACTIVE,
    }
    return GraphSnapshot(nodes=["A", "B", "C"], edges=edges, states=states)


class TestSnapshotToJson:
    def test_snapshot_to_json(self) -> None:
        """Serializes to valid JSON with expected structure."""
        snap = _snapshot_with_data()
        json_str = snap.to_json()

        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)
        assert parsed["nodes"] == ["A", "B", "C"]
        assert len(parsed["edges"]) == 2

        edge_0 = parsed["edges"][0]
        assert edge_0["from_agent"] == "A"
        assert edge_0["to_agent"] == "B"
        assert edge_0["resource"] == "file.txt"
        assert edge_0["created_at"] == 1.0
        assert edge_0["workflow_id"] == "wf-1"

        assert parsed["states"]["A"] == "waiting"
        assert parsed["states"]["B"] == "waiting"
        assert parsed["states"]["C"] == "active"

    def test_empty_snapshot_to_json(self) -> None:
        snap = GraphSnapshot()
        parsed = json.loads(snap.to_json())
        assert parsed["nodes"] == []
        assert parsed["edges"] == []
        assert parsed["states"] == {}


class TestSnapshotToDot:
    def test_snapshot_to_dot(self) -> None:
        """Exports Graphviz DOT format."""
        snap = _snapshot_with_data()
        dot = snap.to_dot()

        assert dot.startswith("digraph WaitForGraph {")
        assert dot.strip().endswith("}")

        # Nodes with state labels
        assert '"A" [label="A (waiting)"]' in dot
        assert '"B" [label="B (waiting)"]' in dot
        assert '"C" [label="C (active)"]' in dot

        # Edges with labels
        assert '"A" -> "B" [label="file.txt"]' in dot
        assert '"B" -> "C" [label=""]' in dot

    def test_empty_snapshot_to_dot(self) -> None:
        snap = GraphSnapshot()
        dot = snap.to_dot()
        assert "digraph WaitForGraph {" in dot
        assert dot.strip().endswith("}")


class TestSnapshotRoundtrip:
    def test_snapshot_roundtrip(self) -> None:
        """JSON serialize -> deserialize -> equal."""
        original = _snapshot_with_data()
        json_str = original.to_json()

        restored = GraphSnapshot.from_json(json_str)

        assert restored.nodes == original.nodes

        assert len(restored.edges) == len(original.edges)
        for orig_edge, rest_edge in zip(original.edges, restored.edges, strict=True):
            assert rest_edge.from_agent == orig_edge.from_agent
            assert rest_edge.to_agent == orig_edge.to_agent
            assert rest_edge.resource == orig_edge.resource
            assert rest_edge.created_at == orig_edge.created_at
            assert rest_edge.workflow_id == orig_edge.workflow_id

        assert restored.states == original.states

    def test_roundtrip_empty(self) -> None:
        """Empty snapshot round-trips correctly."""
        original = GraphSnapshot()
        restored = GraphSnapshot.from_json(original.to_json())
        assert restored.nodes == []
        assert restored.edges == []
        assert restored.states == {}


class TestSnapshotFromJsonErrors:
    def test_from_json_invalid_json(self) -> None:
        """from_json with malformed JSON raises json.JSONDecodeError."""
        import pytest

        with pytest.raises(json.JSONDecodeError):
            GraphSnapshot.from_json("not valid json {{{")

    def test_from_json_missing_keys(self) -> None:
        """from_json with missing required keys raises KeyError."""
        import pytest

        with pytest.raises(KeyError):
            GraphSnapshot.from_json('{"nodes": []}')


class TestSnapshotToDotMissingState:
    def test_to_dot_node_without_state(self) -> None:
        """to_dot defaults to ACTIVE for nodes without a state entry."""
        snap = GraphSnapshot(
            nodes=["A", "B"], edges=[], states={"A": AgentStatus.WAITING}
        )
        dot = snap.to_dot()
        assert '"B" [label="B (active)"]' in dot
        assert '"A" [label="A (waiting)"]' in dot
