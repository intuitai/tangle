# src/tangle/integrations/otel.py

import structlog

from tangle.types import Event, EventType

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
