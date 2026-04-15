# src/tangle/server/app.py

from fastapi import FastAPI

from tangle.monitor import TangleMonitor
from tangle.server.routes import router


def create_app(monitor: TangleMonitor) -> FastAPI:
    app = FastAPI(title="Tangle", version="0.1.0")
    app.state.monitor = monitor
    app.include_router(router, prefix="/v1")

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app
