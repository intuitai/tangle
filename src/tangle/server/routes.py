# src/tangle/server/routes.py

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from tangle.types import DetectionType, Event, EventType, Severity

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class EventRequest(BaseModel):
    type: EventType = Field(description="Event type (wait_for, release, send, ...).")
    workflow_id: str = Field(description="Identifier of the workflow emitting the event.")
    from_agent: str = Field(description="Agent that emitted the event.")
    to_agent: str = Field(default="", description="Target agent, when applicable.")
    resource: str = Field(default="", description="Resource name, when applicable.")
    message_body: str = Field(
        default="",
        description=(
            "Hex-encoded message payload. Non-hex values are accepted and "
            "UTF-8 encoded verbatim for backwards compatibility."
        ),
    )
    timestamp: float | None = Field(
        default=None,
        description="Event timestamp. If omitted, the server assigns `monitor.clock()`.",
    )


class BatchEventRequest(BaseModel):
    events: list[EventRequest]


class EventResponse(BaseModel):
    accepted: bool
    detection: bool
    idempotent_replay: bool = False


class BatchEventResponse(BaseModel):
    accepted: bool
    events_count: int
    detections: int
    idempotent_replay: bool = False


class CycleModel(BaseModel):
    agents: list[str]
    workflow_id: str


class LivelockModel(BaseModel):
    agents: list[str]
    pattern_length: int
    repeat_count: int
    workflow_id: str


class DetectionModel(BaseModel):
    type: str
    severity: str
    resolved: bool
    workflow_id: str
    cycle: CycleModel | None = None
    livelock: LivelockModel | None = None


class DetectionListResponse(BaseModel):
    items: list[DetectionModel]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_event(req: EventRequest, monitor: Any) -> Event:
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


def _detection_workflow(detection: Any) -> str:
    if detection.cycle:
        return str(detection.cycle.workflow_id)
    if detection.livelock:
        return str(detection.livelock.workflow_id)
    return ""


def _detection_resolved(detection: Any) -> bool:
    if detection.cycle:
        return bool(detection.cycle.resolved)
    if detection.livelock:
        return bool(detection.livelock.resolved)
    return False


def _serialize_detection(d: Any) -> dict[str, Any]:
    return {
        "type": d.type.value,
        "severity": d.severity.value,
        "resolved": _detection_resolved(d),
        "workflow_id": _detection_workflow(d),
        "cycle": (
            {"agents": d.cycle.agents, "workflow_id": d.cycle.workflow_id} if d.cycle else None
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


def _idempotency_cache_key(key: str, payload: BaseModel) -> str:
    """Bind the cache key to the request body so the same Idempotency-Key
    with a different payload does not return a stale response."""
    digest = hashlib.sha256(
        json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    return f"{key}:{digest}"


# ---------------------------------------------------------------------------
# Event ingestion
# ---------------------------------------------------------------------------


@router.post(
    "/events",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EventResponse,
    summary="Submit a single event",
    responses={
        401: {"description": "Missing or invalid bearer token"},
        422: {"description": "Validation error"},
    },
)
async def post_event(
    req: EventRequest,
    request: Request,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description=(
                "Client-generated key for deduplicating retries. Requests with "
                "the same key and body within the cache window return the "
                "original response and do not re-process the event."
            ),
        ),
    ] = None,
) -> dict[str, object]:
    monitor = request.app.state.monitor
    cache: Any = getattr(request.app.state, "idempotency", None)

    if idempotency_key and cache is not None and cache.enabled:
        cache_key = _idempotency_cache_key(idempotency_key, req)
        cached = cache.get(cache_key)
        if cached is not None:
            return {**cached, "idempotent_replay": True}

    event = _to_event(req, monitor)
    detection = monitor.process_event(event)
    response: dict[str, object] = {
        "accepted": True,
        "detection": detection is not None,
        "idempotent_replay": False,
    }

    if idempotency_key and cache is not None and cache.enabled:
        cache.put(_idempotency_cache_key(idempotency_key, req), response)

    return response


@router.post(
    "/events/batch",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BatchEventResponse,
    summary="Submit a batch of events",
    responses={
        401: {"description": "Missing or invalid bearer token"},
        422: {"description": "Validation error"},
    },
)
async def post_event_batch(
    req: BatchEventRequest,
    request: Request,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="Deduplication key for the entire batch. See POST /v1/events.",
        ),
    ] = None,
) -> dict[str, object]:
    monitor = request.app.state.monitor
    cache: Any = getattr(request.app.state, "idempotency", None)

    if idempotency_key and cache is not None and cache.enabled:
        cache_key = _idempotency_cache_key(idempotency_key, req)
        cached = cache.get(cache_key)
        if cached is not None:
            return {**cached, "idempotent_replay": True}

    detections = 0
    for event_req in req.events:
        event = _to_event(event_req, monitor)
        if monitor.process_event(event):
            detections += 1
    response: dict[str, object] = {
        "accepted": True,
        "events_count": len(req.events),
        "detections": detections,
        "idempotent_replay": False,
    }

    if idempotency_key and cache is not None and cache.enabled:
        cache.put(_idempotency_cache_key(idempotency_key, req), response)

    return response


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


@router.get(
    "/graph/{workflow_id}",
    summary="Get the wait-for graph for a workflow",
)
async def get_graph(workflow_id: str, request: Request) -> dict[str, object]:
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


@router.get(
    "/detections",
    response_model=DetectionListResponse,
    summary="List detections",
    description=(
        "Returns detections filtered by the supplied query parameters. "
        "Results are paginated; `total` reflects the filtered count, not "
        "the full detection history."
    ),
)
async def get_detections(
    request: Request,
    workflow_id: Annotated[str | None, Query(description="Filter by workflow id")] = None,
    type: Annotated[
        DetectionType | None,
        Query(description="Filter by detection type (deadlock or livelock)"),
    ] = None,
    severity: Annotated[
        Severity | None, Query(description="Filter by severity (warning or critical)")
    ] = None,
    resolved: Annotated[
        bool | None,
        Query(
            description="If omitted, returns only unresolved detections (legacy behavior).",
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    monitor = request.app.state.monitor

    # Read from the monitor's full detection list so callers can request
    # resolved+unresolved. Default (resolved=None) preserves the previous
    # "active only" behavior.
    with monitor._lock:
        all_detections = list(monitor._detections)

    def keep(d: Any) -> bool:
        if workflow_id is not None and _detection_workflow(d) != workflow_id:
            return False
        if type is not None and d.type != type:
            return False
        if severity is not None and d.severity != severity:
            return False
        is_resolved = _detection_resolved(d)
        if resolved is None:
            return not is_resolved
        return is_resolved == resolved

    filtered = [d for d in all_detections if keep(d)]
    page = filtered[offset : offset + limit]
    return {
        "items": [_serialize_detection(d) for d in page],
        "total": len(filtered),
        "limit": limit,
        "offset": offset,
    }


@router.get("/stats", summary="Monitor statistics")
async def get_stats(request: Request) -> dict[str, int]:
    monitor = request.app.state.monitor
    result: dict[str, int] = monitor.stats()
    return result


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus metrics",
    responses={404: {"description": "Metrics not enabled"}},
)
async def get_metrics(request: Request) -> PlainTextResponse:
    monitor = request.app.state.monitor
    if monitor.metrics is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metrics not enabled. Set metrics_enabled=True in TangleConfig.",
        )
    from prometheus_client import generate_latest

    return PlainTextResponse(
        generate_latest(monitor.metrics.registry),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
