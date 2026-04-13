# tests/test_types.py

from __future__ import annotations

import dataclasses

import pytest

from tangle.types import (AgentStatus, Cycle, Detection, DetectionType, Edge,
                          Event, EventType, LivelockPattern, ResolutionAction,
                          Severity)


class TestEventFrozen:
    def test_event_frozen(self) -> None:
        """Assigning to a field on a frozen Event raises FrozenInstanceError."""
        event = Event(
            type=EventType.SEND,
            timestamp=1.0,
            workflow_id="wf-1",
            from_agent="A",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.type = EventType.REGISTER  # type: ignore[misc]

    def test_event_metadata_mutable_caveat(self) -> None:
        """Metadata dict is mutable even though Event is frozen (documented caveat)."""
        event = Event(
            type=EventType.SEND,
            timestamp=1.0,
            workflow_id="wf-1",
            from_agent="A",
            metadata={"key": "original"},
        )
        # Mutating the dict does NOT raise -- this is a known caveat of frozen dataclasses
        event.metadata["key"] = "mutated"
        assert event.metadata["key"] == "mutated"

        event.metadata["new_key"] = "value"
        assert "new_key" in event.metadata


class TestEventDefaults:
    def test_event_default_fields(self) -> None:
        """Event has sensible defaults for optional fields."""
        event = Event(
            type=EventType.WAIT_FOR,
            timestamp=0.0,
            workflow_id="wf-1",
            from_agent="A",
        )
        assert event.to_agent == ""
        assert event.resource == ""
        assert event.message_body == b""
        assert event.metadata == {}


class TestEnumValues:
    def test_agent_status_values(self) -> None:
        assert AgentStatus.ACTIVE.value == "active"
        assert AgentStatus.WAITING.value == "waiting"
        assert AgentStatus.COMPLETED.value == "completed"
        assert AgentStatus.CANCELED.value == "canceled"
        assert len(AgentStatus) == 4

    def test_event_type_values(self) -> None:
        assert EventType.WAIT_FOR.value == "wait_for"
        assert EventType.RELEASE.value == "release"
        assert EventType.SEND.value == "send"
        assert EventType.REGISTER.value == "register"
        assert EventType.COMPLETE.value == "complete"
        assert EventType.CANCEL.value == "cancel"
        assert EventType.PROGRESS.value == "progress"
        assert len(EventType) == 7

    def test_resolution_action_values(self) -> None:
        assert ResolutionAction.ALERT.value == "alert"
        assert ResolutionAction.CANCEL_YOUNGEST.value == "cancel_youngest"
        assert ResolutionAction.CANCEL_ALL.value == "cancel_all"
        assert ResolutionAction.TIEBREAKER.value == "tiebreaker"
        assert ResolutionAction.ESCALATE.value == "escalate"
        assert len(ResolutionAction) == 5


class TestCycle:
    def test_cycle_auto_id(self) -> None:
        """Cycle() generates a unique UUID id by default."""
        c1 = Cycle()
        c2 = Cycle()
        assert c1.id != c2.id
        # Should look like a UUID (36 chars with hyphens)
        assert len(c1.id) == 36
        assert c1.id.count("-") == 4


class TestLivelockPattern:
    def test_livelock_pattern_auto_id(self) -> None:
        """LivelockPattern() generates a unique UUID id by default."""
        lp1 = LivelockPattern()
        lp2 = LivelockPattern()
        assert lp1.id != lp2.id
        assert len(lp1.id) == 36
        assert lp1.id.count("-") == 4

    def test_livelock_pattern_fields(self) -> None:
        """LivelockPattern stores pattern_length and repeat_count."""
        lp = LivelockPattern(
            agents=["A", "B"], pattern_length=3, repeat_count=5, workflow_id="wf-1"
        )
        assert lp.agents == ["A", "B"]
        assert lp.pattern_length == 3
        assert lp.repeat_count == 5
        assert lp.workflow_id == "wf-1"
        assert lp.resolved is False


class TestEdge:
    def test_edge_fields(self) -> None:
        """Edge dataclass stores all expected fields."""
        e = Edge(
            from_agent="A",
            to_agent="B",
            resource="data",
            created_at=1.0,
            workflow_id="wf-1",
        )
        assert e.from_agent == "A"
        assert e.to_agent == "B"
        assert e.resource == "data"
        assert e.created_at == 1.0
        assert e.workflow_id == "wf-1"

    def test_edge_mutable(self) -> None:
        """Edge is mutable (not frozen)."""
        e = Edge(
            from_agent="A",
            to_agent="B",
            resource="data",
            created_at=1.0,
            workflow_id="wf-1",
        )
        e.resource = "new_data"
        assert e.resource == "new_data"


class TestDetectionTypeSeverityEnums:
    def test_detection_type_values(self) -> None:
        assert DetectionType.DEADLOCK.value == "deadlock"
        assert DetectionType.LIVELOCK.value == "livelock"
        assert len(DetectionType) == 2

    def test_severity_values(self) -> None:
        assert Severity.WARNING.value == "warning"
        assert Severity.CRITICAL.value == "critical"
        assert len(Severity) == 2


class TestDetection:
    def test_detection_requires_type_and_severity(self) -> None:
        """Detection requires both type and severity positional/keyword args."""
        with pytest.raises(TypeError):
            Detection()  # type: ignore[call-arg]

        with pytest.raises(TypeError):
            Detection(type=DetectionType.DEADLOCK)  # type: ignore[call-arg]

        with pytest.raises(TypeError):
            Detection(severity=Severity.CRITICAL)  # type: ignore[call-arg]

        # This should succeed
        d = Detection(type=DetectionType.DEADLOCK, severity=Severity.CRITICAL)
        assert d.type == DetectionType.DEADLOCK
        assert d.severity == Severity.CRITICAL
        assert d.cycle is None
        assert d.livelock is None
