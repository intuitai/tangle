# src/tangle/integrations/otel.py

from concurrent import futures
from typing import TYPE_CHECKING

import structlog

from tangle.types import Event, EventType

if TYPE_CHECKING:
    from tangle.monitor import TangleMonitor

    _ServerType = grpc.Server
else:
    _ServerType = object

try:
    import grpc
    from opentelemetry.proto.collector.trace.v1 import (
        trace_service_pb2,
        trace_service_pb2_grpc,
    )

    _GRPC_AVAILABLE = True
except ImportError:
    _GRPC_AVAILABLE = False

_BASE_SERVICER: type = trace_service_pb2_grpc.TraceServiceServicer if _GRPC_AVAILABLE else object

logger = structlog.get_logger("tangle.otel")

# Attribute key constants
_ATTR_AGENT = "tangle.agent.id"
_ATTR_WORKFLOW = "tangle.workflow.id"
_ATTR_EVENT_TYPE = "tangle.event.type"
_ATTR_TARGET = "tangle.target.agent"
_ATTR_RESOURCE = "tangle.resource"
_ATTR_MSG_HASH = "tangle.message.hash"

_EVENT_TYPE_MAP = {
    "wait_for": EventType.WAIT_FOR,
    "release": EventType.RELEASE,
    "send": EventType.SEND,
    "register": EventType.REGISTER,
    "complete": EventType.COMPLETE,
    "cancel": EventType.CANCEL,
    "progress": EventType.PROGRESS,
}


def _extract_attributes(span) -> dict[str, str]:
    """Extract key-value attributes from an OTel span."""
    attrs: dict[str, str] = {}
    for kv in span.attributes:
        key = kv.key
        value = kv.value
        if hasattr(value, "string_value") and value.string_value:
            attrs[key] = value.string_value
        elif hasattr(value, "int_value"):
            attrs[key] = str(value.int_value)
    return attrs


def parse_span_to_event(span) -> Event | None:
    """Extract a Tangle Event from an OTel span."""
    attrs = _extract_attributes(span)

    agent_id = attrs.get(_ATTR_AGENT)
    workflow_id = attrs.get(_ATTR_WORKFLOW)
    event_type_str = attrs.get(_ATTR_EVENT_TYPE)

    if not agent_id or not workflow_id or not event_type_str:
        return None

    event_type = _EVENT_TYPE_MAP.get(event_type_str)
    if event_type is None:
        return None

    msg_hash = attrs.get(_ATTR_MSG_HASH, "")
    try:
        message_body = bytes.fromhex(msg_hash) if msg_hash else b""
    except ValueError:
        message_body = b""

    return Event(
        type=event_type,
        timestamp=span.start_time_unix_nano / 1e9,
        workflow_id=workflow_id,
        from_agent=agent_id,
        to_agent=attrs.get(_ATTR_TARGET, ""),
        resource=attrs.get(_ATTR_RESOURCE, ""),
        message_body=message_body,
    )


class TangleTraceServicer(_BASE_SERVICER):
    """gRPC servicer that feeds OTel spans into TangleMonitor."""

    def __init__(self, monitor: "TangleMonitor") -> None:
        self._monitor = monitor

    def Export(self, request: object, context: object) -> object:
        if not _GRPC_AVAILABLE:
            raise RuntimeError("grpc/opentelemetry extras not installed")
        for resource_spans in request.resource_spans:  # type: ignore[union-attr]
            for scope_spans in resource_spans.scope_spans:
                for span in scope_spans.spans:
                    event = parse_span_to_event(span)
                    if event is not None:
                        self._monitor.process_event(event)
        return trace_service_pb2.ExportTraceServiceResponse()  # type: ignore[union-attr]


class OTelCollectorError(Exception):
    """Raised when the OTel collector cannot start."""


class OTelCollector:
    """Background gRPC server that receives OTLP trace spans."""

    def __init__(self, monitor: "TangleMonitor", port: int = 4317) -> None:
        self._monitor = monitor
        self._port = port
        self._server: _ServerType | None = None

    def start(self) -> None:
        if not _GRPC_AVAILABLE:
            raise OTelCollectorError("grpc extras not installed; install tangle[otel]")
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))  # type: ignore[union-attr]
        trace_service_pb2_grpc.add_TraceServiceServicer_to_server(  # type: ignore[union-attr]
            TangleTraceServicer(self._monitor), self._server
        )
        self._server.add_insecure_port(f"[::]:{self._port}")  # type: ignore[union-attr]
        self._server.start()  # type: ignore[union-attr]
        logger.info("otel_collector_started", port=self._port)

    def stop(self, grace: float = 5.0) -> None:
        if self._server:
            self._server.stop(grace)  # type: ignore[union-attr]
            logger.info("otel_collector_stopped")
