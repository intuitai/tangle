# tests/integrations/test_mcp.py

from __future__ import annotations

import json
from typing import Any

import pytest

from tangle.config import TangleConfig
from tangle.integrations.mcp import (
    MCP_PROTOCOL_VERSION,
    TangleMCPServer,
    create_mcp_server,
)
from tangle.monitor import TangleMonitor
from tangle.types import AgentStatus
from tests.conftest import FakeClock


@pytest.fixture()
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def monitor(fake_clock: FakeClock) -> TangleMonitor:
    config = TangleConfig(cycle_check_interval=999_999.0, livelock_min_repeats=2)
    return TangleMonitor(config=config, clock=fake_clock)


@pytest.fixture()
def mcp_server(monitor: TangleMonitor) -> TangleMCPServer:
    return create_mcp_server(monitor)


def _payload(result: dict) -> Any:
    """Parse the JSON payload out of the first text-content block."""
    assert result["content"], "expected at least one content block"
    block = result["content"][0]
    assert block["type"] == "text"
    return json.loads(block["text"])


# ---------------------------------------------------------------------------
# Metadata / schema contract
# ---------------------------------------------------------------------------


class TestServerMetadata:
    def test_server_info_advertises_protocol_and_capabilities(
        self, mcp_server: TangleMCPServer
    ) -> None:
        info = mcp_server.server_info()
        assert info["protocolVersion"] == MCP_PROTOCOL_VERSION
        assert info["serverInfo"]["name"] == "tangle"
        assert "tools" in info["capabilities"]
        assert "resources" in info["capabilities"]

    def test_create_mcp_server_custom_name(self, monitor: TangleMonitor) -> None:
        server = create_mcp_server(monitor, name="my-tangle")
        assert server.name == "my-tangle"
        assert server.server_info()["serverInfo"]["name"] == "my-tangle"

    def test_server_exposes_monitor(
        self, mcp_server: TangleMCPServer, monitor: TangleMonitor
    ) -> None:
        assert mcp_server.monitor is monitor


class TestToolRegistry:
    def test_list_tools_returns_expected_set(self, mcp_server: TangleMCPServer) -> None:
        names = {t["name"] for t in mcp_server.list_tools()}
        assert names == {
            "tangle_register_agent",
            "tangle_wait_for",
            "tangle_release",
            "tangle_send_message",
            "tangle_complete_agent",
            "tangle_cancel_agent",
            "tangle_report_progress",
            "tangle_get_snapshot",
            "tangle_get_detections",
            "tangle_get_stats",
            "tangle_reset_workflow",
        }

    def test_every_tool_has_json_schema(self, mcp_server: TangleMCPServer) -> None:
        for tool in mcp_server.list_tools():
            schema = tool["inputSchema"]
            assert schema["type"] == "object"
            assert "properties" in schema
            assert isinstance(tool["description"], str) and tool["description"]

    def test_list_tools_returns_independent_copies(self, mcp_server: TangleMCPServer) -> None:
        first = mcp_server.list_tools()
        first[0]["name"] = "mutated"
        second = mcp_server.list_tools()
        assert second[0]["name"] != "mutated"

    def test_required_fields_for_wait_for(self, mcp_server: TangleMCPServer) -> None:
        tool = next(t for t in mcp_server.list_tools() if t["name"] == "tangle_wait_for")
        assert set(tool["inputSchema"]["required"]) == {
            "workflow_id",
            "from_agent",
            "to_agent",
        }


class TestResourceRegistry:
    def test_list_resources_exposes_expected_uris(self, mcp_server: TangleMCPServer) -> None:
        uris = {r["uri"] for r in mcp_server.list_resources()}
        assert uris == {
            "tangle://stats",
            "tangle://detections",
            "tangle://graph/{workflow_id}",
        }

    def test_resource_entries_have_json_mime(self, mcp_server: TangleMCPServer) -> None:
        for res in mcp_server.list_resources():
            assert res["mimeType"] == "application/json"
            assert res["name"]
            assert res["description"]


# ---------------------------------------------------------------------------
# Tool dispatch: happy paths
# ---------------------------------------------------------------------------


class TestRegisterAndInspect:
    def test_register_agent_creates_graph_node(
        self, mcp_server: TangleMCPServer, monitor: TangleMonitor
    ) -> None:
        result = mcp_server.call_tool(
            "tangle_register_agent",
            {"workflow_id": "wf-1", "agent_id": "A"},
        )
        assert result["isError"] is False
        body = _payload(result)
        assert body == {"registered": "A", "workflow_id": "wf-1"}
        assert "A" in monitor.snapshot("wf-1").nodes

    def test_get_snapshot_returns_nodes_and_edges(self, mcp_server: TangleMCPServer) -> None:
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "A"})
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "B"})
        mcp_server.call_tool(
            "tangle_wait_for",
            {"workflow_id": "wf", "from_agent": "A", "to_agent": "B", "resource": "db"},
        )

        result = mcp_server.call_tool("tangle_get_snapshot", {"workflow_id": "wf"})
        body = _payload(result)

        assert body["workflow_id"] == "wf"
        assert set(body["nodes"]) == {"A", "B"}
        assert len(body["edges"]) == 1
        assert body["edges"][0]["from_agent"] == "A"
        assert body["edges"][0]["to_agent"] == "B"
        assert body["edges"][0]["resource"] == "db"
        assert body["states"]["A"] == AgentStatus.WAITING.value

    def test_get_stats_reports_counters(self, mcp_server: TangleMCPServer) -> None:
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "A"})
        result = mcp_server.call_tool("tangle_get_stats", {})
        body = _payload(result)
        assert body["events_processed"] == 1
        assert body["graph_nodes"] == 1


class TestWaitForAndDeadlock:
    def test_wait_for_reports_no_deadlock_on_non_cycle(self, mcp_server: TangleMCPServer) -> None:
        for agent in ("A", "B", "C"):
            mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": agent})
        result = mcp_server.call_tool(
            "tangle_wait_for",
            {"workflow_id": "wf", "from_agent": "A", "to_agent": "B"},
        )
        body = _payload(result)
        assert body["deadlock_detected"] is False
        assert body["detections"] == []

    def test_wait_for_detects_cycle(
        self, mcp_server: TangleMCPServer, monitor: TangleMonitor
    ) -> None:
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "A"})
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "B"})
        mcp_server.call_tool(
            "tangle_wait_for",
            {"workflow_id": "wf", "from_agent": "A", "to_agent": "B"},
        )
        result = mcp_server.call_tool(
            "tangle_wait_for",
            {"workflow_id": "wf", "from_agent": "B", "to_agent": "A"},
        )

        body = _payload(result)
        assert body["deadlock_detected"] is True
        assert len(body["detections"]) == 1
        det = body["detections"][0]
        assert det["type"] == "deadlock"
        assert set(det["cycle"]["agents"]) == {"A", "B"}
        assert det["cycle"]["workflow_id"] == "wf"

        # Also visible via inspection tool and resource
        inspect = _payload(mcp_server.call_tool("tangle_get_detections", {}))
        assert len(inspect) == 1

        resource = _payload(mcp_server.read_resource("tangle://detections"))
        assert len(resource) == 1
        assert monitor.active_detections()

    def test_wait_for_isolates_per_workflow(self, mcp_server: TangleMCPServer) -> None:
        # Cycle in wf-A
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf-A", "agent_id": "X"})
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf-A", "agent_id": "Y"})
        mcp_server.call_tool(
            "tangle_wait_for",
            {"workflow_id": "wf-A", "from_agent": "X", "to_agent": "Y"},
        )
        mcp_server.call_tool(
            "tangle_wait_for",
            {"workflow_id": "wf-A", "from_agent": "Y", "to_agent": "X"},
        )

        # Non-cyclic edge in wf-B — should report no deadlock *for wf-B*
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf-B", "agent_id": "P"})
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf-B", "agent_id": "Q"})
        wf_b = _payload(
            mcp_server.call_tool(
                "tangle_wait_for",
                {"workflow_id": "wf-B", "from_agent": "P", "to_agent": "Q"},
            )
        )
        assert wf_b["deadlock_detected"] is False
        assert wf_b["detections"] == []

    def test_release_removes_edge(
        self, mcp_server: TangleMCPServer, monitor: TangleMonitor
    ) -> None:
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "A"})
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "B"})
        mcp_server.call_tool(
            "tangle_wait_for",
            {"workflow_id": "wf", "from_agent": "A", "to_agent": "B"},
        )
        assert len(monitor.snapshot("wf").edges) == 1

        result = mcp_server.call_tool(
            "tangle_release",
            {"workflow_id": "wf", "from_agent": "A", "to_agent": "B"},
        )
        assert result["isError"] is False
        assert len(monitor.snapshot("wf").edges) == 0


class TestSendAndLivelock:
    def test_send_message_records_event(
        self, mcp_server: TangleMCPServer, monitor: TangleMonitor
    ) -> None:
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "A"})
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "B"})
        before = monitor.stats()["events_processed"]
        result = mcp_server.call_tool(
            "tangle_send_message",
            {
                "workflow_id": "wf",
                "from_agent": "A",
                "to_agent": "B",
                "body": "hello",
            },
        )
        assert result["isError"] is False
        body = _payload(result)
        assert body["livelock_detected"] is False
        assert monitor.stats()["events_processed"] == before + 1

    def test_send_message_triggers_livelock(
        self, fake_clock: FakeClock, monitor: TangleMonitor
    ) -> None:
        # A ping-pong pattern with min_repeats=2 will trip quickly.
        server = create_mcp_server(monitor)
        server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "A"})
        server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "B"})

        for _ in range(8):
            fake_clock.advance(1.0)
            server.call_tool(
                "tangle_send_message",
                {"workflow_id": "wf", "from_agent": "A", "to_agent": "B", "body": "ping"},
            )
            fake_clock.advance(1.0)
            server.call_tool(
                "tangle_send_message",
                {"workflow_id": "wf", "from_agent": "B", "to_agent": "A", "body": "pong"},
            )

        detections = _payload(server.call_tool("tangle_get_detections", {}))
        assert any(d["type"] == "livelock" for d in detections)

    def test_report_progress_resets_livelock_buffers(
        self, mcp_server: TangleMCPServer, monitor: TangleMonitor
    ) -> None:
        before = monitor.stats()["events_processed"]
        result = mcp_server.call_tool(
            "tangle_report_progress",
            {"workflow_id": "wf", "description": "finished draft"},
        )
        body = _payload(result)
        assert body["progress_recorded"] is True
        assert monitor.stats()["events_processed"] == before + 1


class TestLifecycleTools:
    def test_complete_marks_agent_completed(
        self, mcp_server: TangleMCPServer, monitor: TangleMonitor
    ) -> None:
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "A"})
        mcp_server.call_tool("tangle_complete_agent", {"workflow_id": "wf", "agent_id": "A"})
        snap = monitor.snapshot("wf")
        assert snap.states["A"] == AgentStatus.COMPLETED

    def test_cancel_marks_agent_canceled(
        self, mcp_server: TangleMCPServer, monitor: TangleMonitor
    ) -> None:
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "A"})
        mcp_server.call_tool(
            "tangle_cancel_agent",
            {"workflow_id": "wf", "agent_id": "A", "reason": "bored"},
        )
        snap = monitor.snapshot("wf")
        assert snap.states["A"] == AgentStatus.CANCELED

    def test_reset_workflow_clears_state(
        self, mcp_server: TangleMCPServer, monitor: TangleMonitor
    ) -> None:
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "A"})
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "B"})
        mcp_server.call_tool(
            "tangle_wait_for",
            {"workflow_id": "wf", "from_agent": "A", "to_agent": "B"},
        )
        mcp_server.call_tool(
            "tangle_wait_for",
            {"workflow_id": "wf", "from_agent": "B", "to_agent": "A"},
        )
        assert monitor.active_detections()

        mcp_server.call_tool("tangle_reset_workflow", {"workflow_id": "wf"})

        assert monitor.snapshot("wf").nodes == []
        assert monitor.active_detections() == []


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class TestResources:
    def test_read_stats_resource(self, mcp_server: TangleMCPServer) -> None:
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "A"})
        result = mcp_server.read_resource("tangle://stats")
        assert result["isError"] is False
        body = _payload(result)
        assert body["events_processed"] == 1

    def test_read_graph_resource(self, mcp_server: TangleMCPServer) -> None:
        mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf", "agent_id": "A"})
        result = mcp_server.read_resource("tangle://graph/wf")
        body = _payload(result)
        assert body["workflow_id"] == "wf"
        assert "A" in body["nodes"]

    def test_read_graph_resource_requires_workflow_id(self, mcp_server: TangleMCPServer) -> None:
        result = mcp_server.read_resource("tangle://graph/")
        assert result["isError"] is True

    def test_read_detections_resource_empty(self, mcp_server: TangleMCPServer) -> None:
        body = _payload(mcp_server.read_resource("tangle://detections"))
        assert body == []

    def test_read_unknown_resource_returns_error(self, mcp_server: TangleMCPServer) -> None:
        result = mcp_server.read_resource("tangle://does-not-exist")
        assert result["isError"] is True


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_unknown_tool_returns_error(self, mcp_server: TangleMCPServer) -> None:
        result = mcp_server.call_tool("not_a_real_tool", {})
        assert result["isError"] is True
        body = _payload(result)
        assert "unknown tool" in body["error"]

    def test_missing_required_argument_returns_error(self, mcp_server: TangleMCPServer) -> None:
        # agent_id is required
        result = mcp_server.call_tool("tangle_register_agent", {"workflow_id": "wf"})
        assert result["isError"] is True
        body = _payload(result)
        assert "agent_id" in body["error"]

    def test_none_arguments_treated_as_empty(self, mcp_server: TangleMCPServer) -> None:
        result = mcp_server.call_tool("tangle_get_stats", None)
        assert result["isError"] is False

    def test_text_content_is_valid_json_when_payload_is_object(
        self, mcp_server: TangleMCPServer
    ) -> None:
        result = mcp_server.call_tool("tangle_get_stats", {})
        raw = result["content"][0]["text"]
        # Must round-trip through json without loss
        assert isinstance(json.loads(raw), dict)
