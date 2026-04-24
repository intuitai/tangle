# tests/integration/test_server_e2e.py

from __future__ import annotations

import httpx
import pytest

from tangle import TangleConfig, TangleMonitor
from tangle.server.app import create_app
from tests.conftest import FakeClock


@pytest.mark.integration
class TestServerE2E:
    async def test_full_event_lifecycle(self):
        """Submit events via HTTP, query graph and detections."""
        clock = FakeClock()
        monitor = TangleMonitor(
            config=TangleConfig(cycle_check_interval=999),
            clock=clock,
        )
        app = create_app(monitor)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Register agents
            for agent in ["A", "B"]:
                resp = await client.post(
                    "/v1/events",
                    json={
                        "type": "register",
                        "workflow_id": "wf-1",
                        "from_agent": agent,
                    },
                )
                assert resp.status_code == 202

            # Create deadlock
            await client.post(
                "/v1/events",
                json={
                    "type": "wait_for",
                    "workflow_id": "wf-1",
                    "from_agent": "A",
                    "to_agent": "B",
                    "resource": "x",
                },
            )
            resp = await client.post(
                "/v1/events",
                json={
                    "type": "wait_for",
                    "workflow_id": "wf-1",
                    "from_agent": "B",
                    "to_agent": "A",
                    "resource": "y",
                },
            )
            assert resp.status_code == 202
            data = resp.json()
            assert data["detection"] is True

            # Query graph
            resp = await client.get("/v1/graph/wf-1")
            assert resp.status_code == 200
            graph = resp.json()
            assert "A" in graph["nodes"]
            assert "B" in graph["nodes"]

            # Query detections
            resp = await client.get("/v1/detections")
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] >= 1
            assert body["items"][0]["type"] == "deadlock"

            # Query stats
            resp = await client.get("/v1/stats")
            assert resp.status_code == 200
            stats = resp.json()
            assert stats["active_detections"] >= 1

    async def test_healthz(self):
        """Health endpoint returns ok."""
        monitor = TangleMonitor()
        app = create_app(monitor)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

    async def test_batch_events(self):
        """Batch endpoint accepts multiple events and returns count."""
        clock = FakeClock()
        monitor = TangleMonitor(
            config=TangleConfig(cycle_check_interval=999),
            clock=clock,
        )
        app = create_app(monitor)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/events/batch",
                json={
                    "events": [
                        {"type": "register", "workflow_id": "wf-b", "from_agent": "X"},
                        {"type": "register", "workflow_id": "wf-b", "from_agent": "Y"},
                        {
                            "type": "wait_for",
                            "workflow_id": "wf-b",
                            "from_agent": "X",
                            "to_agent": "Y",
                        },
                    ]
                },
            )
            assert resp.status_code == 202
            data = resp.json()
            assert data["events_count"] == 3
