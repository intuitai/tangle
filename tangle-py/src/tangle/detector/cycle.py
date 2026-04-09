# src/tangle/detector/cycle.py

from collections import deque

from tangle.graph.wfg import WaitForGraph
from tangle.types import AgentID, Cycle, Edge


class CycleDetector:
    """Detects cycles (deadlocks) in the Wait-For Graph."""

    def __init__(self, graph: WaitForGraph, max_depth: int = 20) -> None:
        self._graph = graph
        self._max_depth = max_depth

    def on_edge_added(self, edge: Edge) -> Cycle | None:
        """
        Runs incremental DFS from edge.to_agent looking for edge.from_agent.
        If found, a cycle exists.
        """
        target = edge.from_agent
        start = edge.to_agent

        # Self-loop check
        if target == start:
            return Cycle(
                agents=[target],
                edges=[edge],
                workflow_id=edge.workflow_id,
            )

        workflow_id = edge.workflow_id

        # DFS from start looking for target — restricted to the same workflow
        visited: set[AgentID] = set()
        path: list[AgentID] = []

        def dfs(node: AgentID, depth: int) -> bool:
            if depth > self._max_depth:
                return False
            if node == target:
                return True
            if node in visited:
                return False
            visited.add(node)
            path.append(node)
            for out_edge in self._graph.outgoing(node, workflow_id=workflow_id):
                if dfs(out_edge.to_agent, depth + 1):
                    return True
            path.pop()
            return False

        if dfs(start, 1):
            # Cycle: target -> ... -> path -> target
            cycle_agents = (
                [target] + path + [target] if path else [target, start, target]
            )
            # Deduplicate closing node
            cycle_agents_unique = cycle_agents[:-1]  # Remove the closing duplicate

            # Collect edges in the cycle
            cycle_edges = [edge]  # The triggering edge (from_agent -> to_agent)
            for i in range(len(path)):
                src = path[i]
                dst = path[i + 1] if i + 1 < len(path) else target
                for out_edge in self._graph.outgoing(src, workflow_id=workflow_id):
                    if out_edge.to_agent == dst:
                        cycle_edges.append(out_edge)
                        break

            return Cycle(
                agents=cycle_agents_unique,
                edges=cycle_edges,
                workflow_id=edge.workflow_id,
            )
        return None

    def full_scan(self) -> list[Cycle]:
        """
        Kahn's algorithm run per-workflow to avoid cross-workflow false positives.
        Nodes not in topological order within their workflow are in cycles.
        """
        all_edges = self._graph.all_edges()

        # Group edges and nodes by workflow
        workflow_edges: dict[str, list[Edge]] = {}
        workflow_nodes: dict[str, set[AgentID]] = {}
        for edge in all_edges:
            wf = edge.workflow_id
            workflow_edges.setdefault(wf, []).append(edge)
            workflow_nodes.setdefault(wf, set()).add(edge.from_agent)
            workflow_nodes.setdefault(wf, set()).add(edge.to_agent)

        # Registered-only nodes (no edges) cannot be in a cycle, so only
        # edge-participating nodes need scanning.
        cycles: list[Cycle] = []

        for wf_id, edges in workflow_edges.items():
            nodes = list(workflow_nodes.get(wf_id, set()))
            if not nodes:
                continue

            # Build adjacency and in-degree within this workflow only
            in_degree: dict[AgentID, int] = {n: 0 for n in nodes}
            adj: dict[AgentID, list[AgentID]] = {n: [] for n in nodes}
            edge_map: dict[tuple[AgentID, AgentID], Edge] = {}

            for edge in edges:
                if edge.from_agent in in_degree and edge.to_agent in in_degree:
                    adj[edge.from_agent].append(edge.to_agent)
                    in_degree[edge.to_agent] += 1
                    edge_map[(edge.from_agent, edge.to_agent)] = edge

            # Kahn's topological sort
            queue: deque[AgentID] = deque(n for n, d in in_degree.items() if d == 0)
            topo_order: list[AgentID] = []

            while queue:
                node = queue.popleft()
                topo_order.append(node)
                for neighbor in adj[node]:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        queue.append(neighbor)

            # Nodes not in topo_order are in cycles
            cycle_nodes = set(nodes) - set(topo_order)
            if not cycle_nodes:
                continue

            # Trace exact cycles via DFS among cycle nodes
            visited: set[AgentID] = set()
            for start_node in cycle_nodes:
                if start_node in visited:
                    continue
                cycle_path = self._trace_cycle(start_node, cycle_nodes, adj)
                if cycle_path:
                    cycle_edges: list[Edge] = []
                    for i in range(len(cycle_path)):
                        src = cycle_path[i]
                        dst = cycle_path[(i + 1) % len(cycle_path)]
                        if (src, dst) in edge_map:
                            cycle_edges.append(edge_map[(src, dst)])
                    cycles.append(
                        Cycle(
                            agents=cycle_path,
                            edges=cycle_edges,
                            workflow_id=wf_id,
                        )
                    )
                    visited.update(cycle_path)

        return cycles

    def _trace_cycle(
        self,
        start: AgentID,
        cycle_nodes: set[AgentID],
        adj: dict[AgentID, list[AgentID]],
    ) -> list[AgentID]:
        """Trace a single cycle starting from start among cycle_nodes."""
        visited: set[AgentID] = set()
        path: list[AgentID] = []

        def dfs(node: AgentID) -> list[AgentID] | None:
            if node in visited:
                if node == start and len(path) > 0:
                    return list(path)
                return None
            visited.add(node)
            path.append(node)
            for neighbor in adj.get(node, []):
                if neighbor in cycle_nodes:
                    result = dfs(neighbor)
                    if result is not None:
                        return result
            path.pop()
            return None

        result = dfs(start)
        return result if result else []
