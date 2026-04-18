# src/tangle/integrations/mcp.py

"""MCP (Model Context Protocol) wrapper for a Tangle monitor.

Exposes the Tangle SDK surface as MCP-compatible tools and resources so that
LLM-driven agents can instrument their own workflows and inspect detections
without importing the Tangle Python package directly.

The wrapper is a pure-Python adapter: it produces JSON-schema tool definitions
and dispatches calls against a ``TangleMonitor``. It does not take a hard
dependency on the ``mcp`` SDK — a server integration can consume
``list_tools()``/``call_tool()`` verbatim.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tangle.types import DetectionType

if TYPE_CHECKING:
    from tangle.monitor import TangleMonitor

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


MCP_PROTOCOL_VERSION = "2024-11-05"


def _text_content(payload: Any) -> list[dict[str, Any]]:
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str, indent=2)
    return [{"type": "text", "text": text}]


def _error_result(message: str) -> dict[str, Any]:
    return {"content": _text_content({"error": message}), "isError": True}


def _ok_result(payload: Any) -> dict[str, Any]:
    return {"content": _text_content(payload), "isError": False}


_WORKFLOW_ID_SCHEMA = {
    "type": "string",
    "description": "Workflow identifier (namespace for agents and edges)",
}
_AGENT_ID_SCHEMA = {"type": "string", "description": "Unique agent identifier"}


class TangleMCPServer:
    """MCP-compatible facade over a ``TangleMonitor``.

    The methods on this class mirror the MCP wire contract: ``list_tools``,
    ``call_tool``, ``list_resources``, ``read_resource``. A transport layer
    (stdio, SSE, HTTP) can forward requests straight through.
    """

    def __init__(self, monitor: TangleMonitor, *, name: str = "tangle") -> None:
        self._monitor = monitor
        self._name = name
        self._tools = self._build_tools()
        self._resources = self._build_resources()
        self._dispatch: dict[str, ToolHandler] = {
            t["name"]: getattr(self, f"_tool_{t['name']}") for t in self._tools
        }

    @property
    def name(self) -> str:
        return self._name

    @property
    def monitor(self) -> TangleMonitor:
        return self._monitor

    # ------------------------------------------------------------------
    # MCP "initialize" result
    # ------------------------------------------------------------------
    def server_info(self) -> dict[str, Any]:
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": {"name": self._name, "version": "0.1.0"},
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False, "subscribe": False},
            },
        }

    # ------------------------------------------------------------------
    # Tool registry
    # ------------------------------------------------------------------
    def list_tools(self) -> list[dict[str, Any]]:
        return [dict(t) for t in self._tools]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        handler = self._dispatch.get(name)
        if handler is None:
            return _error_result(f"unknown tool: {name}")
        args = arguments or {}
        try:
            return handler(args)
        except KeyError as exc:
            return _error_result(f"missing required argument: {exc.args[0]}")
        except (TypeError, ValueError) as exc:
            return _error_result(str(exc))

    # ------------------------------------------------------------------
    # Resource registry
    # ------------------------------------------------------------------
    def list_resources(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._resources]

    def read_resource(self, uri: str) -> dict[str, Any]:
        if uri == "tangle://stats":
            return _ok_result(self._monitor.stats())
        if uri == "tangle://detections":
            return _ok_result(self._serialize_detections(self._monitor.active_detections()))
        if uri.startswith("tangle://graph/"):
            workflow_id = uri[len("tangle://graph/") :]
            if not workflow_id:
                return _error_result("workflow_id is required in tangle://graph/<workflow_id>")
            return _ok_result(self._serialize_snapshot(workflow_id))
        return _error_result(f"unknown resource uri: {uri}")

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------
    def _build_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "tangle_register_agent",
                "description": "Register an agent as active in a workflow.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "workflow_id": _WORKFLOW_ID_SCHEMA,
                        "agent_id": _AGENT_ID_SCHEMA,
                    },
                    "required": ["workflow_id", "agent_id"],
                },
            },
            {
                "name": "tangle_wait_for",
                "description": (
                    "Record that one agent is waiting on another. May trigger a "
                    "deadlock detection if this edge closes a cycle."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "workflow_id": _WORKFLOW_ID_SCHEMA,
                        "from_agent": _AGENT_ID_SCHEMA,
                        "to_agent": _AGENT_ID_SCHEMA,
                        "resource": {
                            "type": "string",
                            "description": "Resource the waiter is blocked on (optional)",
                            "default": "",
                        },
                    },
                    "required": ["workflow_id", "from_agent", "to_agent"],
                },
            },
            {
                "name": "tangle_release",
                "description": "Release a previously recorded wait-for edge.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "workflow_id": _WORKFLOW_ID_SCHEMA,
                        "from_agent": _AGENT_ID_SCHEMA,
                        "to_agent": _AGENT_ID_SCHEMA,
                    },
                    "required": ["workflow_id", "from_agent", "to_agent"],
                },
            },
            {
                "name": "tangle_send_message",
                "description": (
                    "Record an inter-agent message. Livelock detection compares "
                    "message-body digests across the recent window."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "workflow_id": _WORKFLOW_ID_SCHEMA,
                        "from_agent": _AGENT_ID_SCHEMA,
                        "to_agent": _AGENT_ID_SCHEMA,
                        "body": {
                            "type": "string",
                            "description": "Message body (UTF-8 text). Optional.",
                            "default": "",
                        },
                    },
                    "required": ["workflow_id", "from_agent", "to_agent"],
                },
            },
            {
                "name": "tangle_complete_agent",
                "description": "Mark an agent as completed. Clears its edges.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "workflow_id": _WORKFLOW_ID_SCHEMA,
                        "agent_id": _AGENT_ID_SCHEMA,
                    },
                    "required": ["workflow_id", "agent_id"],
                },
            },
            {
                "name": "tangle_cancel_agent",
                "description": "Cancel an agent. Clears its edges.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "workflow_id": _WORKFLOW_ID_SCHEMA,
                        "agent_id": _AGENT_ID_SCHEMA,
                        "reason": {
                            "type": "string",
                            "description": "Optional free-form reason",
                            "default": "",
                        },
                    },
                    "required": ["workflow_id", "agent_id"],
                },
            },
            {
                "name": "tangle_report_progress",
                "description": (
                    "Report forward progress on a workflow. Resets livelock "
                    "detection buffers for that workflow."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "workflow_id": _WORKFLOW_ID_SCHEMA,
                        "description": {
                            "type": "string",
                            "description": "Human-readable progress note",
                            "default": "",
                        },
                    },
                    "required": ["workflow_id"],
                },
            },
            {
                "name": "tangle_get_snapshot",
                "description": "Return the wait-for graph snapshot for a workflow.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"workflow_id": _WORKFLOW_ID_SCHEMA},
                    "required": ["workflow_id"],
                },
            },
            {
                "name": "tangle_get_detections",
                "description": "Return currently unresolved detections (deadlocks, livelocks).",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "tangle_get_stats",
                "description": "Return monitor counters (events, graph size, active detections).",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "tangle_reset_workflow",
                "description": (
                    "Clear all graph state, livelock buffers, and detections for a workflow."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {"workflow_id": _WORKFLOW_ID_SCHEMA},
                    "required": ["workflow_id"],
                },
            },
        ]

    def _build_resources(self) -> list[dict[str, Any]]:
        return [
            {
                "uri": "tangle://stats",
                "name": "Tangle monitor stats",
                "description": "Counters for events processed, graph size, active detections.",
                "mimeType": "application/json",
            },
            {
                "uri": "tangle://detections",
                "name": "Active detections",
                "description": "All unresolved deadlock and livelock detections.",
                "mimeType": "application/json",
            },
            {
                "uri": "tangle://graph/{workflow_id}",
                "name": "Workflow wait-for graph",
                "description": "Per-workflow wait-for graph snapshot (nodes, edges, states).",
                "mimeType": "application/json",
            },
        ]

    # ------------------------------------------------------------------
    # Tool handlers (one per declared tool, keyed by name)
    # ------------------------------------------------------------------
    def _tool_tangle_register_agent(self, args: dict[str, Any]) -> dict[str, Any]:
        self._monitor.register(args["workflow_id"], args["agent_id"])
        return _ok_result({"registered": args["agent_id"], "workflow_id": args["workflow_id"]})

    def _tool_tangle_wait_for(self, args: dict[str, Any]) -> dict[str, Any]:
        workflow_id = args["workflow_id"]
        from_agent = args["from_agent"]
        to_agent = args["to_agent"]
        self._monitor.wait_for(
            workflow_id=workflow_id,
            from_agent=from_agent,
            to_agent=to_agent,
            resource=args.get("resource", ""),
        )
        detections = self._detections_for(workflow_id, DetectionType.DEADLOCK)
        return _ok_result(
            {
                "from_agent": from_agent,
                "to_agent": to_agent,
                "workflow_id": workflow_id,
                "deadlock_detected": bool(detections),
                "detections": detections,
            }
        )

    def _tool_tangle_release(self, args: dict[str, Any]) -> dict[str, Any]:
        self._monitor.release(args["workflow_id"], args["from_agent"], args["to_agent"])
        return _ok_result(
            {
                "released": True,
                "from_agent": args["from_agent"],
                "to_agent": args["to_agent"],
                "workflow_id": args["workflow_id"],
            }
        )

    def _tool_tangle_send_message(self, args: dict[str, Any]) -> dict[str, Any]:
        workflow_id = args["workflow_id"]
        body = args.get("body", "")
        self._monitor.send(
            workflow_id=workflow_id,
            from_agent=args["from_agent"],
            to_agent=args["to_agent"],
            body=body.encode() if isinstance(body, str) else bytes(body),
        )
        detections = self._detections_for(workflow_id, DetectionType.LIVELOCK)
        return _ok_result(
            {
                "from_agent": args["from_agent"],
                "to_agent": args["to_agent"],
                "workflow_id": workflow_id,
                "livelock_detected": bool(detections),
                "detections": detections,
            }
        )

    def _tool_tangle_complete_agent(self, args: dict[str, Any]) -> dict[str, Any]:
        self._monitor.complete(args["workflow_id"], args["agent_id"])
        return _ok_result({"completed": args["agent_id"], "workflow_id": args["workflow_id"]})

    def _tool_tangle_cancel_agent(self, args: dict[str, Any]) -> dict[str, Any]:
        self._monitor.cancel(args["workflow_id"], args["agent_id"], reason=args.get("reason", ""))
        return _ok_result({"canceled": args["agent_id"], "workflow_id": args["workflow_id"]})

    def _tool_tangle_report_progress(self, args: dict[str, Any]) -> dict[str, Any]:
        self._monitor.report_progress(args["workflow_id"], description=args.get("description", ""))
        return _ok_result({"workflow_id": args["workflow_id"], "progress_recorded": True})

    def _tool_tangle_get_snapshot(self, args: dict[str, Any]) -> dict[str, Any]:
        return _ok_result(self._serialize_snapshot(args["workflow_id"]))

    def _tool_tangle_get_detections(self, _args: dict[str, Any]) -> dict[str, Any]:
        return _ok_result(self._serialize_detections(self._monitor.active_detections()))

    def _tool_tangle_get_stats(self, _args: dict[str, Any]) -> dict[str, Any]:
        return _ok_result(self._monitor.stats())

    def _tool_tangle_reset_workflow(self, args: dict[str, Any]) -> dict[str, Any]:
        self._monitor.reset_workflow(args["workflow_id"])
        return _ok_result({"reset": True, "workflow_id": args["workflow_id"]})

    # ------------------------------------------------------------------
    # Serializers
    # ------------------------------------------------------------------
    def _serialize_snapshot(self, workflow_id: str) -> dict[str, Any]:
        snap = self._monitor.snapshot(workflow_id)
        return {
            "workflow_id": workflow_id,
            "nodes": snap.nodes,
            "edges": [
                {
                    "from_agent": e.from_agent,
                    "to_agent": e.to_agent,
                    "resource": e.resource,
                    "workflow_id": e.workflow_id,
                }
                for e in snap.edges
            ],
            "states": {k: v.value for k, v in snap.states.items()},
        }

    def _serialize_detections(self, detections: list[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for d in detections:
            entry: dict[str, Any] = {
                "type": d.type.value,
                "severity": d.severity.value,
            }
            if d.cycle is not None:
                entry["cycle"] = {
                    "id": d.cycle.id,
                    "agents": list(d.cycle.agents),
                    "workflow_id": d.cycle.workflow_id,
                    "resolved": d.cycle.resolved,
                }
            if d.livelock is not None:
                entry["livelock"] = {
                    "id": d.livelock.id,
                    "agents": list(d.livelock.agents),
                    "pattern_length": d.livelock.pattern_length,
                    "repeat_count": d.livelock.repeat_count,
                    "workflow_id": d.livelock.workflow_id,
                    "resolved": d.livelock.resolved,
                }
            out.append(entry)
        return out

    def _detections_for(
        self, workflow_id: str, detection_type: DetectionType
    ) -> list[dict[str, Any]]:
        matches = []
        for d in self._monitor.active_detections():
            if d.type != detection_type:
                continue
            if (
                detection_type == DetectionType.DEADLOCK
                and d.cycle is not None
                and d.cycle.workflow_id == workflow_id
            ) or (
                detection_type == DetectionType.LIVELOCK
                and d.livelock is not None
                and d.livelock.workflow_id == workflow_id
            ):
                matches.append(d)
        return self._serialize_detections(matches)


def create_mcp_server(monitor: TangleMonitor, *, name: str = "tangle") -> TangleMCPServer:
    """Build a ``TangleMCPServer`` for the given monitor."""
    return TangleMCPServer(monitor, name=name)
