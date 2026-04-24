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
    async def test_get_graph(self, client: httpx.AsyncClient, monitor: TangleMonitor) -> None:
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
    async def test_get_detections(self, client: httpx.AsyncClient, monitor: TangleMonitor) -> None:
        """GET /v1/detections returns a paginated envelope of detections."""
        # Create a deadlock
        monitor.register("wf-det", "A")
        monitor.register("wf-det", "B")
        monitor.wait_for("wf-det", "A", "B")
        monitor.wait_for("wf-det", "B", "A")

        resp = await client.get("/v1/detections")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert body["total"] >= 1
        assert body["limit"] == 100
        assert body["offset"] == 0
        items = body["items"]
        assert len(items) >= 1
        assert items[0]["type"] == "deadlock"
        assert items[0]["severity"] == "critical"
        assert items[0]["resolved"] is False
        assert items[0]["workflow_id"] == "wf-det"
        assert items[0]["cycle"] is not None
        assert set(items[0]["cycle"]["agents"]) >= {"A", "B"}


# ---------------------------------------------------------------------------
# GET /v1/stats
# ---------------------------------------------------------------------------


class TestGetStats:
    async def test_get_stats(self, client: httpx.AsyncClient, monitor: TangleMonitor) -> None:
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
        """POST /v1/events with an unknown event type returns 422 (not 500).

        EventType is used as the Pydantic field type, so Pydantic validates it
        and returns a 422 Unprocessable Entity for invalid values.
        """

        payload = {
            "type": "invalid_type",
            "workflow_id": "wf-1",
            "from_agent": "A",
        }
        resp = await client.post("/v1/events", json=payload)
        assert resp.status_code == 422


class TestPostEventHexFallback:
    async def test_post_event_non_hex_message_body(self, client: httpx.AsyncClient) -> None:
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

    async def test_post_event_zero_timestamp_preserved(
        self, client: httpx.AsyncClient, monitor: TangleMonitor, fake_clock: FakeClock
    ) -> None:
        """timestamp=0.0 must be preserved, not replaced with the monitor clock."""
        # Advance the fake clock so clock() != 0.0
        fake_clock.advance(10.0)

        payload = {
            "type": "register",
            "workflow_id": "wf-ts",
            "from_agent": "A",
            "timestamp": 0.0,
        }
        resp = await client.post("/v1/events", json=payload)
        assert resp.status_code == 202

        # The stored event should have timestamp 0.0, not 10.0
        events = monitor._store.get_workflow_events("wf-ts")
        assert len(events) == 1
        assert events[0].timestamp == 0.0


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
        livelock_dets = [d for d in body["items"] if d.get("livelock") is not None]
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


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_monitor(fake_clock: FakeClock) -> TangleMonitor:
    config = TangleConfig(
        cycle_check_interval=999_999.0,
        api_auth_token="s3cret",
    )
    return TangleMonitor(config=config, clock=fake_clock)


@pytest.fixture()
async def auth_client(auth_monitor: TangleMonitor) -> httpx.AsyncClient:
    app = create_app(auth_monitor)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


class TestAuth:
    async def test_missing_token_rejected(self, auth_client: httpx.AsyncClient) -> None:
        resp = await auth_client.get("/v1/stats")
        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate", "").lower().startswith("bearer")

    async def test_wrong_token_rejected(self, auth_client: httpx.AsyncClient) -> None:
        resp = await auth_client.get("/v1/stats", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    async def test_non_bearer_scheme_rejected(self, auth_client: httpx.AsyncClient) -> None:
        resp = await auth_client.get("/v1/stats", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert resp.status_code == 401

    async def test_valid_token_accepted(self, auth_client: httpx.AsyncClient) -> None:
        resp = await auth_client.get("/v1/stats", headers={"Authorization": "Bearer s3cret"})
        assert resp.status_code == 200

    async def test_healthz_always_public(self, auth_client: httpx.AsyncClient) -> None:
        """Liveness probe must not require auth."""
        resp = await auth_client.get("/healthz")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Detection filters & pagination
# ---------------------------------------------------------------------------


class TestDetectionFilters:
    async def test_filter_by_workflow_id(
        self, client: httpx.AsyncClient, monitor: TangleMonitor
    ) -> None:
        monitor.register("wf-a", "A")
        monitor.register("wf-a", "B")
        monitor.wait_for("wf-a", "A", "B")
        monitor.wait_for("wf-a", "B", "A")

        monitor.register("wf-b", "X")
        monitor.register("wf-b", "Y")
        monitor.wait_for("wf-b", "X", "Y")
        monitor.wait_for("wf-b", "Y", "X")

        resp = await client.get("/v1/detections", params={"workflow_id": "wf-a"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["workflow_id"] == "wf-a" for item in body["items"])

    async def test_filter_by_type(self, client: httpx.AsyncClient, monitor: TangleMonitor) -> None:
        monitor.register("wf-t", "A")
        monitor.register("wf-t", "B")
        monitor.wait_for("wf-t", "A", "B")
        monitor.wait_for("wf-t", "B", "A")

        resp = await client.get("/v1/detections", params={"type": "livelock"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        resp = await client.get("/v1/detections", params={"type": "deadlock"})
        assert resp.json()["total"] >= 1

    async def test_invalid_type_rejected(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/v1/detections", params={"type": "nonsense"})
        assert resp.status_code == 422

    async def test_limit_and_offset(
        self, client: httpx.AsyncClient, monitor: TangleMonitor
    ) -> None:
        # Create multiple deadlocks across different workflows.
        for i in range(5):
            wf = f"wf-{i}"
            monitor.register(wf, "A")
            monitor.register(wf, "B")
            monitor.wait_for(wf, "A", "B")
            monitor.wait_for(wf, "B", "A")

        resp = await client.get("/v1/detections", params={"limit": 2, "offset": 0})
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] >= 5
        assert body["limit"] == 2

        resp2 = await client.get("/v1/detections", params={"limit": 2, "offset": 2})
        body2 = resp2.json()
        assert len(body2["items"]) == 2
        assert body2["offset"] == 2

    async def test_resolved_filter_default_excludes_resolved(
        self, client: httpx.AsyncClient, monitor: TangleMonitor
    ) -> None:
        monitor.register("wf-r", "A")
        monitor.register("wf-r", "B")
        monitor.wait_for("wf-r", "A", "B")
        monitor.wait_for("wf-r", "B", "A")

        # Mark the detection resolved.
        with monitor._lock:
            assert monitor._detections[0].cycle is not None
            monitor._detections[0].cycle.resolved = True

        resp_default = await client.get("/v1/detections")
        assert resp_default.json()["total"] == 0

        resp_all = await client.get("/v1/detections", params={"resolved": "true"})
        assert resp_all.json()["total"] >= 1


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    async def test_same_key_same_body_returns_cached(
        self, client: httpx.AsyncClient, monitor: TangleMonitor
    ) -> None:
        payload = {"type": "register", "workflow_id": "wf-idem", "from_agent": "A"}
        headers = {"Idempotency-Key": "abc-123"}

        first = await client.post("/v1/events", json=payload, headers=headers)
        assert first.status_code == 202
        assert first.json()["idempotent_replay"] is False

        second = await client.post("/v1/events", json=payload, headers=headers)
        assert second.status_code == 202
        assert second.json()["idempotent_replay"] is True

        # Exactly one event should have been processed by the monitor.
        events = monitor._store.get_workflow_events("wf-idem")
        assert len(events) == 1

    async def test_same_key_different_body_does_not_collide(
        self, client: httpx.AsyncClient, monitor: TangleMonitor
    ) -> None:
        headers = {"Idempotency-Key": "same-key"}
        p1 = {"type": "register", "workflow_id": "wf-x", "from_agent": "A"}
        p2 = {"type": "register", "workflow_id": "wf-x", "from_agent": "B"}

        r1 = await client.post("/v1/events", json=p1, headers=headers)
        r2 = await client.post("/v1/events", json=p2, headers=headers)
        assert r1.json()["idempotent_replay"] is False
        assert r2.json()["idempotent_replay"] is False
        assert len(monitor._store.get_workflow_events("wf-x")) == 2

    async def test_batch_idempotency(self, client: httpx.AsyncClient) -> None:
        payload = {
            "events": [
                {"type": "register", "workflow_id": "wf-bi", "from_agent": "A"},
                {"type": "register", "workflow_id": "wf-bi", "from_agent": "B"},
            ],
        }
        headers = {"Idempotency-Key": "batch-key"}
        first = await client.post("/v1/events/batch", json=payload, headers=headers)
        second = await client.post("/v1/events/batch", json=payload, headers=headers)
        assert first.json()["idempotent_replay"] is False
        assert second.json()["idempotent_replay"] is True
        assert second.json()["events_count"] == 2

    async def test_no_key_always_processes(
        self, client: httpx.AsyncClient, monitor: TangleMonitor
    ) -> None:
        payload = {"type": "register", "workflow_id": "wf-nk", "from_agent": "A"}
        await client.post("/v1/events", json=payload)
        await client.post("/v1/events", json=payload)
        assert len(monitor._store.get_workflow_events("wf-nk")) == 2
