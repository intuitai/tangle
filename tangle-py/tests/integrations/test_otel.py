# tests/integrations/test_otel.py

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from tangle.integrations.otel import parse_span_to_event
from tangle.types import EventType

# ---------------------------------------------------------------------------
# Mock span objects
# ---------------------------------------------------------------------------


@dataclass
class MockValue:
    """Mimics an OTel attribute value with a string_value field."""

    string_value: str = ""


@dataclass
class MockKeyValue:
    """Mimics an OTel attribute key-value pair."""

    key: str = ""
    value: MockValue = field(default_factory=MockValue)


@dataclass
class MockSpan:
    """Mimics an OTel span with start_time_unix_nano and attributes."""

    start_time_unix_nano: int = 0
    attributes: list[MockKeyValue] = field(default_factory=list)


def _make_span(
    agent_id: str | None = None,
    workflow_id: str | None = None,
    event_type: str | None = None,
    target_agent: str | None = None,
    resource: str | None = None,
    message_hash: str | None = None,
    start_time_ns: int = 1_000_000_000,
) -> MockSpan:
    """Build a mock span with the given tangle attributes."""
    attrs: list[MockKeyValue] = []
    if agent_id is not None:
        attrs.append(MockKeyValue(key="tangle.agent.id", value=MockValue(string_value=agent_id)))
    if workflow_id is not None:
        attrs.append(
            MockKeyValue(key="tangle.workflow.id", value=MockValue(string_value=workflow_id))
        )
    if event_type is not None:
        attrs.append(
            MockKeyValue(key="tangle.event.type", value=MockValue(string_value=event_type))
        )
    if target_agent is not None:
        attrs.append(
            MockKeyValue(key="tangle.target.agent", value=MockValue(string_value=target_agent))
        )
    if resource is not None:
        attrs.append(MockKeyValue(key="tangle.resource", value=MockValue(string_value=resource)))
    if message_hash is not None:
        attrs.append(
            MockKeyValue(key="tangle.message.hash", value=MockValue(string_value=message_hash))
        )
    return MockSpan(start_time_unix_nano=start_time_ns, attributes=attrs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOtelSpanParser:
    def test_parse_wait_for_span(self) -> None:
        """Span with tangle.event.type=wait_for produces EventType.WAIT_FOR."""
        span = _make_span(
            agent_id="agent-A",
            workflow_id="wf-1",
            event_type="wait_for",
            target_agent="agent-B",
            resource="data",
        )
        event = parse_span_to_event(span)

        assert event is not None
        assert event.type == EventType.WAIT_FOR
        assert event.from_agent == "agent-A"
        assert event.to_agent == "agent-B"
        assert event.workflow_id == "wf-1"
        assert event.resource == "data"

    def test_parse_send_span(self) -> None:
        """Span with send + message hash produces correct Event."""
        hex_hash = "deadbeef01020304"
        span = _make_span(
            agent_id="sender",
            workflow_id="wf-2",
            event_type="send",
            target_agent="receiver",
            message_hash=hex_hash,
        )
        event = parse_span_to_event(span)

        assert event is not None
        assert event.type == EventType.SEND
        assert event.from_agent == "sender"
        assert event.to_agent == "receiver"
        assert event.message_body == bytes.fromhex(hex_hash)

    def test_parse_register_span(self) -> None:
        """Span with register type produces EventType.REGISTER."""
        span = _make_span(
            agent_id="new-agent",
            workflow_id="wf-3",
            event_type="register",
        )
        event = parse_span_to_event(span)

        assert event is not None
        assert event.type == EventType.REGISTER
        assert event.from_agent == "new-agent"
        assert event.workflow_id == "wf-3"

    def test_ignore_non_tangle_span(self) -> None:
        """Span without any tangle.* attributes returns None."""
        span = MockSpan(
            start_time_unix_nano=1_000_000_000,
            attributes=[
                MockKeyValue(key="http.method", value=MockValue(string_value="GET")),
                MockKeyValue(key="http.url", value=MockValue(string_value="https://example.com")),
            ],
        )
        assert parse_span_to_event(span) is None

    def test_ignore_missing_workflow(self) -> None:
        """Span with agent_id but no workflow_id returns None."""
        span = _make_span(
            agent_id="agent-A",
            workflow_id=None,
            event_type="register",
        )
        assert parse_span_to_event(span) is None

    def test_ignore_unknown_event_type(self) -> None:
        """Span with tangle.event.type=foo returns None."""
        span = _make_span(
            agent_id="agent-A",
            workflow_id="wf-1",
            event_type="foo",
        )
        assert parse_span_to_event(span) is None

    def test_timestamp_conversion(self) -> None:
        """start_time_unix_nano is correctly converted to seconds."""
        ns = 1_700_000_000_000_000_000  # 1.7e18 ns = 1.7e9 seconds
        span = _make_span(
            agent_id="agent-A",
            workflow_id="wf-1",
            event_type="register",
            start_time_ns=ns,
        )
        event = parse_span_to_event(span)

        assert event is not None
        assert event.timestamp == pytest.approx(1_700_000_000.0)

    def test_missing_optional_attributes(self) -> None:
        """Span without tangle.resource or tangle.target.agent gets empty defaults."""
        span = _make_span(
            agent_id="agent-A",
            workflow_id="wf-1",
            event_type="complete",
            # No target_agent, resource, or message_hash
        )
        event = parse_span_to_event(span)

        assert event is not None
        assert event.type == EventType.COMPLETE
        assert event.to_agent == ""
        assert event.resource == ""
        assert event.message_body == b""

    def test_ignore_missing_agent_id(self) -> None:
        """Span with workflow_id and event_type but missing agent_id returns None."""
        span = _make_span(
            agent_id=None,
            workflow_id="wf-1",
            event_type="register",
        )
        assert parse_span_to_event(span) is None

    def test_parse_release_span(self) -> None:
        """Span with tangle.event.type=release produces EventType.RELEASE."""
        span = _make_span(
            agent_id="agent-A",
            workflow_id="wf-1",
            event_type="release",
            target_agent="agent-B",
        )
        event = parse_span_to_event(span)
        assert event is not None
        assert event.type == EventType.RELEASE
        assert event.to_agent == "agent-B"

    def test_parse_cancel_span(self) -> None:
        """Span with tangle.event.type=cancel produces EventType.CANCEL."""
        span = _make_span(
            agent_id="agent-A",
            workflow_id="wf-1",
            event_type="cancel",
        )
        event = parse_span_to_event(span)
        assert event is not None
        assert event.type == EventType.CANCEL

    def test_parse_progress_span(self) -> None:
        """Span with tangle.event.type=progress produces EventType.PROGRESS."""
        span = _make_span(
            agent_id="agent-A",
            workflow_id="wf-1",
            event_type="progress",
            resource="step-3",
        )
        event = parse_span_to_event(span)
        assert event is not None
        assert event.type == EventType.PROGRESS
        assert event.resource == "step-3"

    def test_invalid_hex_message_hash(self) -> None:
        """Invalid hex in message_hash falls back to empty bytes."""
        span = _make_span(
            agent_id="agent-A",
            workflow_id="wf-1",
            event_type="send",
            target_agent="agent-B",
            message_hash="not_hex_ZZZZ",
        )
        event = parse_span_to_event(span)
        assert event is not None
        assert event.message_body == b""


class TestOtelIntValue:
    def test_extract_int_value(self) -> None:
        """_extract_attributes handles int_value attributes."""

        @dataclass
        class IntValue:
            string_value: str = ""
            int_value: int = 0

        span = MockSpan(
            start_time_unix_nano=1_000_000_000,
            attributes=[
                MockKeyValue(key="tangle.agent.id", value=MockValue(string_value="agent-A")),
                MockKeyValue(key="tangle.workflow.id", value=MockValue(string_value="wf-1")),
                MockKeyValue(key="tangle.event.type", value=MockValue(string_value="register")),
                MockKeyValue(key="tangle.int.attr", value=IntValue(int_value=42)),
            ],
        )
        event = parse_span_to_event(span)
        assert event is not None
        assert event.type == EventType.REGISTER

    def test_extract_value_no_string_no_int(self) -> None:
        """Attributes with neither string_value nor int_value are skipped."""

        @dataclass
        class EmptyValue:
            string_value: str = ""

        span = MockSpan(
            start_time_unix_nano=1_000_000_000,
            attributes=[
                MockKeyValue(key="tangle.agent.id", value=MockValue(string_value="agent-A")),
                MockKeyValue(key="tangle.workflow.id", value=MockValue(string_value="wf-1")),
                MockKeyValue(key="tangle.event.type", value=MockValue(string_value="register")),
                MockKeyValue(key="ignored.attr", value=EmptyValue(string_value="")),
            ],
        )
        event = parse_span_to_event(span)
        assert event is not None  # Skipped attr doesn't break parsing
