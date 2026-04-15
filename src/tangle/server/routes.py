# src/tangle/server/routes.py

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from tangle.types import Event, EventType

router = APIRouter()


class EventRequest(BaseModel):
    type: EventType  # Pydantic validates and returns 422 for unknown event types
    workflow_id: str
    from_agent: str
    to_agent: str = ""
    resource: str = ""
    message_body: str = ""  # hex-encoded
    timestamp: float | None = None


class BatchEventRequest(BaseModel):
    events: list[EventRequest]


def _to_event(req: EventRequest, monitor) -> Event:
    try:
        body = bytes.fromhex(req.message_body) if req.message_body else b""
    except ValueError:
        body = req.message_body.encode() if req.message_body else b""
    return Event(
        type=req.type,
        timestamp=req.timestamp if req.timestamp is not None else monitor.clock(),
        workflow_id=req.workflow_id,
        from_agent=req.from_agent,
        to_agent=req.to_agent,
        resource=req.resource,
        message_body=body,
    )


@router.post("/events", status_code=202)
async def post_event(req: EventRequest, request: Request):
    monitor = request.app.state.monitor
    event = _to_event(req, monitor)
    detection = monitor.process_event(event)
    return {"accepted": True, "detection": detection is not None}


@router.post("/events/batch", status_code=202)
async def post_event_batch(req: BatchEventRequest, request: Request):
    monitor = request.app.state.monitor
    detections = 0
    for event_req in req.events:
        event = _to_event(event_req, monitor)
        detection = monitor.process_event(event)
        if detection:
            detections += 1
    return {"accepted": True, "events_count": len(req.events), "detections": detections}


@router.get("/graph/{workflow_id}")
async def get_graph(workflow_id: str, request: Request):
    monitor = request.app.state.monitor
    snapshot = monitor.snapshot(workflow_id)
    return {
        "nodes": snapshot.nodes,
        "edges": [
            {
                "from_agent": e.from_agent,
                "to_agent": e.to_agent,
                "resource": e.resource,
            }
            for e in snapshot.edges
        ],
        "states": {k: v.value for k, v in snapshot.states.items()},
    }


@router.get("/detections")
async def get_detections(request: Request):
    monitor = request.app.state.monitor
    detections = monitor.active_detections()
    return [
        {
            "type": d.type.value,
            "severity": d.severity.value,
            "cycle": (
                {
                    "agents": d.cycle.agents,
                    "workflow_id": d.cycle.workflow_id,
                }
                if d.cycle
                else None
            ),
            "livelock": (
                {
                    "agents": d.livelock.agents,
                    "pattern_length": d.livelock.pattern_length,
                    "repeat_count": d.livelock.repeat_count,
                    "workflow_id": d.livelock.workflow_id,
                }
                if d.livelock
                else None
            ),
        }
        for d in detections
    ]


@router.get("/stats")
async def get_stats(request: Request):
    monitor = request.app.state.monitor
    return monitor.stats()


@router.get("/metrics", response_class=PlainTextResponse)
async def get_metrics(request: Request):
    monitor = request.app.state.monitor
    if monitor.metrics is None:
        return PlainTextResponse(
            "Metrics not enabled. Set metrics_enabled=True in TangleConfig.\n",
            status_code=404,
        )
    from prometheus_client import generate_latest

    return PlainTextResponse(
        generate_latest(monitor.metrics.registry),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
