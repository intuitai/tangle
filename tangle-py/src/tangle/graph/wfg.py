# src/tangle/graph/wfg.py

import threading
from collections import defaultdict
from typing import TYPE_CHECKING

from tangle.types import AgentID, AgentStatus, Edge

if TYPE_CHECKING:
    from tangle.graph.snapshot import GraphSnapshot


class WaitForGraph:
    """Thread-safe directed graph tracking agent blocking dependencies."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._edges: dict[AgentID, dict[AgentID, Edge]] = defaultdict(dict)
        self._states: dict[AgentID, AgentStatus] = {}
        self._join_times: dict[AgentID, float] = {}
        self._workflow_map: dict[AgentID, str] = {}

    def add_edge(self, edge: Edge) -> None:
        with self._lock:
            self._edges[edge.from_agent][edge.to_agent] = edge

    def remove_edge(self, from_agent: AgentID, to_agent: AgentID) -> None:
        with self._lock:
            if from_agent in self._edges:
                self._edges[from_agent].pop(to_agent, None)
                if not self._edges[from_agent]:
                    del self._edges[from_agent]

    def register_agent(
        self, agent_id: AgentID, workflow_id: str, timestamp: float
    ) -> None:
        with self._lock:
            self._states[agent_id] = AgentStatus.ACTIVE
            self._join_times[agent_id] = timestamp
            self._workflow_map[agent_id] = workflow_id

    def set_state(self, agent_id: AgentID, state: AgentStatus) -> None:
        with self._lock:
            self._states[agent_id] = state

    def has_edge(self, from_agent: AgentID, to_agent: AgentID) -> bool:
        with self._lock:
            return to_agent in self._edges.get(from_agent, {})

    def outgoing(self, agent_id: AgentID) -> list[Edge]:
        with self._lock:
            return list(self._edges.get(agent_id, {}).values())

    def all_edges(self) -> list[Edge]:
        with self._lock:
            result: list[Edge] = []
            for targets in self._edges.values():
                result.extend(targets.values())
            return result

    def all_nodes(self) -> list[AgentID]:
        with self._lock:
            return list(self._states.keys())

    def get_join_time(self, agent_id: AgentID) -> float | None:
        with self._lock:
            return self._join_times.get(agent_id)

    def edge_count(self) -> int:
        with self._lock:
            return sum(len(targets) for targets in self._edges.values())

    def node_count(self) -> int:
        with self._lock:
            return len(self._states)

    def agents_in_workflow(self, workflow_id: str) -> list[AgentID]:
        with self._lock:
            return [a for a, wf in self._workflow_map.items() if wf == workflow_id]

    def clear_workflow(self, workflow_id: str) -> None:
        with self._lock:
            agents = [a for a, wf in self._workflow_map.items() if wf == workflow_id]
            for agent in agents:
                self._states.pop(agent, None)
                self._join_times.pop(agent, None)
                self._workflow_map.pop(agent, None)
                self._edges.pop(agent, None)
            # Also remove edges pointing TO these agents
            for src in list(self._edges.keys()):
                for agent in agents:
                    self._edges[src].pop(agent, None)
                if not self._edges[src]:
                    del self._edges[src]

    def get_state(self, agent_id: AgentID) -> AgentStatus | None:
        with self._lock:
            return self._states.get(agent_id)

    def snapshot(self) -> GraphSnapshot:
        from tangle.graph.snapshot import GraphSnapshot

        with self._lock:
            return GraphSnapshot(
                nodes=list(self._states.keys()),
                edges=self.all_edges(),
                states=dict(self._states),
            )
