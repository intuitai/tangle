# src/tangle/graph/snapshot.py

import json
from dataclasses import dataclass, field

from tangle.types import AgentID, AgentStatus, Edge


@dataclass(slots=True)
class GraphSnapshot:
    nodes: list[AgentID] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    states: dict[AgentID, AgentStatus] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "nodes": self.nodes,
                "edges": [
                    {
                        "from_agent": e.from_agent,
                        "to_agent": e.to_agent,
                        "resource": e.resource,
                        "created_at": e.created_at,
                        "workflow_id": e.workflow_id,
                    }
                    for e in self.edges
                ],
                "states": {k: v.value for k, v in self.states.items()},
            },
            indent=2,
        )

    def to_dot(self) -> str:
        lines = ["digraph WaitForGraph {"]
        for node in self.nodes:
            state = self.states.get(node, AgentStatus.ACTIVE)
            lines.append(f'  "{node}" [label="{node} ({state.value})"];')
        for edge in self.edges:
            label = edge.resource or ""
            lines.append(
                f'  "{edge.from_agent}" -> "{edge.to_agent}" [label="{label}"];'
            )
        lines.append("}")
        return "\n".join(lines)

    @classmethod
    def from_json(cls, data: str) -> "GraphSnapshot":
        obj = json.loads(data)
        edges = [
            Edge(
                from_agent=e["from_agent"],
                to_agent=e["to_agent"],
                resource=e["resource"],
                created_at=e["created_at"],
                workflow_id=e["workflow_id"],
            )
            for e in obj["edges"]
        ]
        states = {k: AgentStatus(v) for k, v in obj["states"].items()}
        return cls(nodes=obj["nodes"], edges=edges, states=states)
