# src/tangle/graph/wfg.py

import threading
from collections import defaultdict

from tangle.graph.snapshot import GraphSnapshot
from tangle.types import AgentID, AgentStatus, Edge

# Internal key type: (workflow_id, agent_id)
_NodeKey = tuple[str, AgentID]


class WaitForGraph:
    """Thread-safe directed graph tracking agent blocking dependencies."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Edges keyed by (workflow_id, from_agent) -> {to_agent: Edge}
        self._edges: dict[_NodeKey, dict[AgentID, Edge]] = defaultdict(dict)
        # State keyed by (workflow_id, agent_id)
        self._states: dict[_NodeKey, AgentStatus] = {}
        self._join_times: dict[_NodeKey, float] = {}
        # Track (workflow_id, agent_id) -> workflow_id for agents_in_workflow lookup
        self._workflow_agents: dict[str, set[AgentID]] = defaultdict(set)

    def add_edge(self, edge: Edge) -> None:
        with self._lock:
            self._edges[(edge.workflow_id, edge.from_agent)][edge.to_agent] = edge

    def remove_edge(
        self, from_agent: AgentID, to_agent: AgentID, workflow_id: str = ""
    ) -> None:
        with self._lock:
            if workflow_id:
                key = (workflow_id, from_agent)
                if key in self._edges:
                    self._edges[key].pop(to_agent, None)
                    if not self._edges[key]:
                        del self._edges[key]
            else:
                # Legacy: scan all workflows (used only when workflow_id is unknown)
                for key in list(self._edges.keys()):
                    wf_id, fa = key
                    if fa == from_agent:
                        self._edges[key].pop(to_agent, None)
                        if not self._edges[key]:
                            del self._edges[key]

    def register_agent(
        self, agent_id: AgentID, workflow_id: str, timestamp: float
    ) -> None:
        with self._lock:
            node_key: _NodeKey = (workflow_id, agent_id)
            self._states[node_key] = AgentStatus.ACTIVE
            self._join_times[node_key] = timestamp
            self._workflow_agents[workflow_id].add(agent_id)

    def set_state(
        self, agent_id: AgentID, state: AgentStatus, workflow_id: str = ""
    ) -> None:
        with self._lock:
            if workflow_id:
                self._states[(workflow_id, agent_id)] = state
            else:
                # Legacy fallback: update all workflows that have this agent
                for key in list(self._states.keys()):
                    wf_id, aid = key
                    if aid == agent_id:
                        self._states[key] = state

    def has_edge(
        self, from_agent: AgentID, to_agent: AgentID, workflow_id: str = ""
    ) -> bool:
        with self._lock:
            if workflow_id:
                return to_agent in self._edges.get((workflow_id, from_agent), {})
            else:
                # Legacy: check any workflow
                for key, targets in self._edges.items():
                    wf_id, fa = key
                    if fa == from_agent and to_agent in targets:
                        return True
                return False

    def outgoing(self, agent_id: AgentID, workflow_id: str = "") -> list[Edge]:
        with self._lock:
            if workflow_id:
                return list(self._edges.get((workflow_id, agent_id), {}).values())
            else:
                # Legacy: gather from all workflows
                result: list[Edge] = []
                for key, targets in self._edges.items():
                    wf_id, fa = key
                    if fa == agent_id:
                        result.extend(targets.values())
                return result

    def all_edges(self) -> list[Edge]:
        with self._lock:
            result: list[Edge] = []
            for targets in self._edges.values():
                result.extend(targets.values())
            return result

    def all_nodes(self) -> list[AgentID]:
        with self._lock:
            return [aid for (_, aid) in self._states]

    def get_join_time(self, agent_id: AgentID, workflow_id: str = "") -> float | None:
        with self._lock:
            if workflow_id:
                return self._join_times.get((workflow_id, agent_id))
            else:
                # Legacy: return first match
                for key, t in self._join_times.items():
                    wf_id, aid = key
                    if aid == agent_id:
                        return t
                return None

    def edge_count(self) -> int:
        with self._lock:
            return sum(len(targets) for targets in self._edges.values())

    def node_count(self) -> int:
        with self._lock:
            return len(self._states)

    def agents_in_workflow(self, workflow_id: str) -> list[AgentID]:
        with self._lock:
            return list(self._workflow_agents.get(workflow_id, set()))

    def clear_workflow(self, workflow_id: str) -> None:
        with self._lock:
            agents = self._workflow_agents.pop(workflow_id, set())
            for agent in agents:
                node_key: _NodeKey = (workflow_id, agent)
                self._states.pop(node_key, None)
                self._join_times.pop(node_key, None)
                self._edges.pop(node_key, None)
            # Remove edges (from any workflow) pointing TO agents that are no longer
            # registered in any workflow. Agents shared across workflows are kept.
            for key in list(self._edges.keys()):
                wf_id, fa = key
                for agent in agents:
                    # Only remove the reference if the agent is not in this edge's workflow
                    if agent in self._edges[key] and (wf_id, agent) not in self._states:
                        del self._edges[key][agent]
                if not self._edges[key]:
                    del self._edges[key]

    def outgoing_count(self, agent_id: AgentID, workflow_id: str = "") -> int:
        with self._lock:
            if workflow_id:
                return len(self._edges.get((workflow_id, agent_id), {}))
            else:
                total = 0
                for key, targets in self._edges.items():
                    wf_id, fa = key
                    if fa == agent_id:
                        total += len(targets)
                return total

    def remove_inbound(self, agent_id: AgentID, workflow_id: str = "") -> list[AgentID]:
        """Remove all edges pointing TO agent_id within the workflow. Returns source agents."""
        with self._lock:
            sources: list[AgentID] = []
            for key in list(self._edges.keys()):
                wf_id, fa = key
                if workflow_id and wf_id != workflow_id:
                    continue
                if agent_id in self._edges[key]:
                    del self._edges[key][agent_id]
                    sources.append(fa)
                    if not self._edges[key]:
                        del self._edges[key]
            return sources

    def get_state(self, agent_id: AgentID, workflow_id: str = "") -> AgentStatus | None:
        with self._lock:
            if workflow_id:
                return self._states.get((workflow_id, agent_id))
            else:
                # Legacy: return first match
                for key, state in self._states.items():
                    wf_id, aid = key
                    if aid == agent_id:
                        return state
                return None

    def snapshot(self) -> GraphSnapshot:
        from tangle.graph.snapshot import GraphSnapshot

        with self._lock:
            nodes = [aid for (_, aid) in self._states]
            states = {aid: state for (wf_id, aid), state in self._states.items()}
            return GraphSnapshot(
                nodes=nodes,
                edges=self.all_edges(),
                states=states,
            )
