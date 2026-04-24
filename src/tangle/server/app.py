# src/tangle/server/app.py

from fastapi import Depends, FastAPI

from tangle.monitor import TangleMonitor
from tangle.server.auth import require_auth
from tangle.server.idempotency import IdempotencyCache
from tangle.server.routes import router

API_VERSION = "v1"
"""Current major version of the HTTP API.

Versioning policy: breaking changes require a new path prefix (`/v2`, ...)
and both versions are served in parallel for at least one minor release
before the older surface is removed. Additive changes (new fields, new
endpoints, new optional query params) do not require a new version.
"""


def create_app(monitor: TangleMonitor) -> FastAPI:
    app = FastAPI(
        title="Tangle",
        version="0.1.0",
        summary="Deadlock and livelock detection for multi-agent AI workflows.",
        description=(
            "Tangle's HTTP surface accepts events from agent runtimes and "
            "exposes the wait-for graph, detections, and metrics.\n\n"
            "**Authentication:** when `api_auth_token` is configured, all "
            "`/v1` routes require `Authorization: Bearer <token>`. `/healthz` "
            "is always unauthenticated so it can serve as a liveness probe.\n\n"
            "**Idempotency:** POST `/v1/events` and `/v1/events/batch` accept "
            "an `Idempotency-Key` header. Repeat requests with the same key "
            "within the cache window return the original response without "
            "re-processing events.\n\n"
            "**Versioning:** breaking changes are released under a new path "
            "prefix (e.g. `/v2`). Adding fields or query parameters is "
            "considered backward-compatible."
        ),
    )
    app.state.monitor = monitor
    app.state.idempotency = IdempotencyCache(max_size=monitor._config.api_idempotency_cache_size)
    app.include_router(
        router,
        prefix=f"/{API_VERSION}",
        dependencies=[Depends(require_auth)],
    )

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
