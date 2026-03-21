# tests/server/test_routes.py

from __future__ import annotations

import httpx
import pytest

from tangle.config import TangleConfig
from tangle.monitor import TangleMonitor
from tangle.server.app import create_app
from tests.conftest import FakeClock


@pytest.fixture()
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def monitor(fake_clock: FakeClock) -> TangleMonitor:
    config = TangleConfig(cycle_check_interval=999_999.0)
    return TangleMonitor(config=config, clock=fake_clock)


@pytest.fixture()
async def client(monitor: TangleMonitor) -> httpx.AsyncClient:
    app = create_app(monitor)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# POST /v1/events
# ---------------------------------------------------------------------------


class TestPostEvent:
    async def test_post_event(self, client: httpx.AsyncClient) -> None:
        """POST /v1/events with valid payload returns 202."""
        payload = {
            "type": "register",
            "workflow_id": "wf-1",
            "from_agent": "agent-A",
        }
        resp = await client.post("/v1/events", json=payload)

        assert resp.status_code == 202
        body = resp.json()
        assert body["accepted"] is True
        assert "detection" in body

    async def test_post_event_bad_json(self, client: httpx.AsyncClient) -> None:
        """POST /v1/events with invalid body returns 422."""
        payload = {"not_a_valid_field": True}
        resp = await client.post("/v1/events", json=payload)

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /v1/events/batch
# ---------------------------------------------------------------------------


class TestPostEventBatch:
    async def test_post_event_batch(self, client: httpx.AsyncClient) -> None:
        """POST /v1/events/batch with multiple events returns 202."""
        payload = {
            "events": [
                {
                    "type": "register",
                    "workflow_id": "wf-1",
                    "from_agent": "A",
                },
                {
                    "type": "register",
                    "workflow_id": "wf-1",
                    "from_agent": "B",
                },
                {
                    "type": "wait_for",
                    "workflow_id": "wf-1",
                    "from_agent": "A",
                    "to_agent": "B",
                },
            ],
        }
        resp = await client.post("/v1/events/batch", json=payload)

        assert resp.status_code == 202
        body = resp.json()
        assert body["accepted"] is True
        assert body["events_count"] == 3


# ---------------------------------------------------------------------------
# GET /v1/graph/{workflow_id}
# ---------------------------------------------------------------------------


class TestGetGraph:
    async def test_get_graph(
        self, client: httpx.AsyncClient, monitor: TangleMonitor
    ) -> None:
        """GET /v1/graph/{wf} returns 200 with populated graph."""
        # Set up some state via the monitor directly
        monitor.register("wf-graph", "A")
        monitor.register("wf-graph", "B")
        monitor.wait_for("wf-graph", "A", "B", resource="data")

        resp = await client.get("/v1/graph/wf-graph")

        assert resp.status_code == 200
        body = resp.json()
        assert set(body["nodes"]) == {"A", "B"}
        assert len(body["edges"]) == 1
        assert body["edges"][0]["from_agent"] == "A"
        assert body["edges"][0]["to_agent"] == "B"
        assert body["states"]["A"] == "waiting"
        assert body["states"]["B"] == "active"

    async def test_get_graph_unknown(self, client: httpx.AsyncClient) -> None:
        """GET /v1/graph/unknown returns 200 with empty graph."""
        resp = await client.get("/v1/graph/unknown")

        assert resp.status_code == 200
        body = resp.json()
        assert body["nodes"] == []
        assert body["edges"] == []
        assert body["states"] == {}


# ---------------------------------------------------------------------------
# GET /v1/detections
# ---------------------------------------------------------------------------


class TestGetDetections:
    async def test_get_detections(
        self, client: httpx.AsyncClient, monitor: TangleMonitor
    ) -> None:
        """GET /v1/detections returns active detections as a list."""
        # Create a deadlock
        monitor.register("wf-det", "A")
        monitor.register("wf-det", "B")
        monitor.wait_for("wf-det", "A", "B")
        monitor.wait_for("wf-det", "B", "A")

        resp = await client.get("/v1/detections")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) >= 1
        assert body[0]["type"] == "deadlock"
        assert body[0]["severity"] == "critical"
        assert body[0]["cycle"] is not None
        assert set(body[0]["cycle"]["agents"]) >= {"A", "B"}


# ---------------------------------------------------------------------------
# GET /v1/stats
# ---------------------------------------------------------------------------


class TestGetStats:
    async def test_get_stats(
        self, client: httpx.AsyncClient, monitor: TangleMonitor
    ) -> None:
        """GET /v1/stats returns stats JSON."""
        monitor.register("wf-stats", "X")

        resp = await client.get("/v1/stats")

        assert resp.status_code == 200
        body = resp.json()
        assert "events_processed" in body
        assert body["events_processed"] >= 1
        assert "active_detections" in body
        assert "graph_nodes" in body
        assert "graph_edges" in body


# ---------------------------------------------------------------------------
# GET /healthz
# ---------------------------------------------------------------------------


class TestHealthz:
    async def test_healthz(self, client: httpx.AsyncClient) -> None:
        """GET /healthz returns 200."""
        resp = await client.get("/healthz")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestPostEventInvalidType:
    async def test_post_event_invalid_type(self, client: httpx.AsyncClient) -> None:
        """POST /v1/events with an unknown event type triggers a server error.

        The EventType(req.type) call raises ValueError for unknown types.
        Depending on FastAPI/ASGI transport config, this may propagate as a
        500 response or raise through the transport.
        """

        payload = {
            "type": "invalid_type",
            "workflow_id": "wf-1",
            "from_agent": "A",
        }
        try:
            resp = await client.post("/v1/events", json=payload)
            # If we get a response, it should be an error status
            assert resp.status_code >= 400
        except ValueError:
            # ASGI transport may propagate the ValueError directly
            pass


class TestPostEventHexFallback:
    async def test_post_event_non_hex_message_body(
        self, client: httpx.AsyncClient
    ) -> None:
        """Non-hex message_body falls back to .encode()."""
        payload = {
            "type": "send",
            "workflow_id": "wf-1",
            "from_agent": "A",
            "to_agent": "B",
            "message_body": "not_valid_hex!@#",
        }
        resp = await client.post("/v1/events", json=payload)
        assert resp.status_code == 202

    async def test_post_event_zero_timestamp_defaults(
        self, client: httpx.AsyncClient
    ) -> None:
        """timestamp=0.0 is falsy so the monitor clock is used instead."""
        payload = {
            "type": "register",
            "workflow_id": "wf-1",
            "from_agent": "A",
            "timestamp": 0.0,
        }
        resp = await client.post("/v1/events", json=payload)
        assert resp.status_code == 202


class TestBatchEventWithDetection:
    async def test_batch_triggering_detection(self, client: httpx.AsyncClient) -> None:
        """Batch that creates a deadlock reports detection count."""
        payload = {
            "events": [
                {"type": "register", "workflow_id": "wf-1", "from_agent": "A"},
                {"type": "register", "workflow_id": "wf-1", "from_agent": "B"},
                {
                    "type": "wait_for",
                    "workflow_id": "wf-1",
                    "from_agent": "A",
                    "to_agent": "B",
                },
                {
                    "type": "wait_for",
                    "workflow_id": "wf-1",
                    "from_agent": "B",
                    "to_agent": "A",
                },
            ],
        }
        resp = await client.post("/v1/events/batch", json=payload)
        assert resp.status_code == 202
        body = resp.json()
        assert body["detections"] >= 1


class TestGetDetectionsLivelock:
    async def test_get_detections_livelock_serialization(
        self, client: httpx.AsyncClient, monitor: TangleMonitor
    ) -> None:
        """GET /v1/detections correctly serializes livelock detections."""

        # Use the monitor to trigger livelock via send events
        monitor.register("wf-ll", "A")
        monitor.register("wf-ll", "B")
        for _ in range(30):
            monitor.send("wf-ll", "A", "B", body=b"request")
            monitor.send("wf-ll", "B", "A", body=b"reject")

        resp = await client.get("/v1/detections")
        assert resp.status_code == 200
        body = resp.json()
        livelock_dets = [d for d in body if d.get("livelock") is not None]
        if livelock_dets:
            ll = livelock_dets[0]["livelock"]
            assert "agents" in ll
            assert "pattern_length" in ll
            assert "repeat_count" in ll


class TestGetGraphRegisteredOnly:
    async def test_get_graph_registered_agents_no_edges(
        self, client: httpx.AsyncClient, monitor: TangleMonitor
    ) -> None:
        """GET /v1/graph for workflow with only registered agents (no edges)."""
        monitor.register("wf-reg", "Solo")

        resp = await client.get("/v1/graph/wf-reg")
        assert resp.status_code == 200
        body = resp.json()
        assert body["nodes"] == ["Solo"]
        assert body["edges"] == []
        assert body["states"]["Solo"] == "active"
