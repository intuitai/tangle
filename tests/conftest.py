# tests/conftest.py

from __future__ import annotations

import pytest

from tangle.config import TangleConfig
from tangle.monitor import TangleMonitor
from tangle.types import Detection, DetectionType, Event, EventType, Severity

# ---------------------------------------------------------------------------
# FakeClock
# ---------------------------------------------------------------------------


class FakeClock:
    """Deterministic clock for tests. Starts at 1000.0."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


# ---------------------------------------------------------------------------
# MockResolver
# ---------------------------------------------------------------------------


class MockResolver:
    """Records detections passed to resolve(). Has an optional failure mode."""

    def __init__(self) -> None:
        self._detections: list[Detection] = []
        self.should_fail: bool = False

    @property
    def name(self) -> str:
        return "mock"

    @property
    def is_notification(self) -> bool:
        return False

    @property
    def count(self) -> int:
        return len(self._detections)

    @property
    def last(self) -> Detection | None:
        return self._detections[-1] if self._detections else None

    def resolve(self, detection: Detection) -> None:
        self._detections.append(detection)
        if self.should_fail:
            raise RuntimeError("MockResolver forced failure")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def monitor(fake_clock: FakeClock) -> TangleMonitor:
    """Monitor with a deterministic clock and periodic scanning disabled."""
    config = TangleConfig(cycle_check_interval=999_999.0)
    return TangleMonitor(config=config, clock=fake_clock)


@pytest.fixture()
def mock_resolver() -> MockResolver:
    return MockResolver()


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_event(
    event_type: EventType,
    workflow_id: str = "wf-1",
    from_agent: str = "A",
    to_agent: str = "",
    resource: str = "",
    timestamp: float = 1.0,
    message_body: bytes = b"",
    metadata: dict[str, str] | None = None,
) -> Event:
    return Event(
        type=event_type,
        timestamp=timestamp,
        workflow_id=workflow_id,
        from_agent=from_agent,
        to_agent=to_agent,
        resource=resource,
        message_body=message_body,
        metadata=metadata or {},
    )


def make_detection(
    detection_type: DetectionType = DetectionType.DEADLOCK,
    severity: Severity = Severity.CRITICAL,
) -> Detection:
    return Detection(type=detection_type, severity=severity)


# ---------------------------------------------------------------------------
# Scenario fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def deadlock_2() -> list[Event]:
    """A->B, B->A  (classic 2-agent deadlock)."""
    return [
        make_event(EventType.REGISTER, from_agent="A", timestamp=1.0),
        make_event(EventType.REGISTER, from_agent="B", timestamp=2.0),
        make_event(EventType.WAIT_FOR, from_agent="A", to_agent="B", timestamp=3.0),
        make_event(EventType.WAIT_FOR, from_agent="B", to_agent="A", timestamp=4.0),
    ]


@pytest.fixture()
def deadlock_3() -> list[Event]:
    """A->B->C->A  (3-agent cycle)."""
    return [
        make_event(EventType.REGISTER, from_agent="A", timestamp=1.0),
        make_event(EventType.REGISTER, from_agent="B", timestamp=2.0),
        make_event(EventType.REGISTER, from_agent="C", timestamp=3.0),
        make_event(EventType.WAIT_FOR, from_agent="A", to_agent="B", timestamp=4.0),
        make_event(EventType.WAIT_FOR, from_agent="B", to_agent="C", timestamp=5.0),
        make_event(EventType.WAIT_FOR, from_agent="C", to_agent="A", timestamp=6.0),
    ]


@pytest.fixture()
def livelock_pingpong() -> list[Event]:
    """A->B 'request', B->A 'reject' repeated N times (ping-pong livelock)."""
    events: list[Event] = [
        make_event(EventType.REGISTER, from_agent="A", timestamp=1.0),
        make_event(EventType.REGISTER, from_agent="B", timestamp=2.0),
    ]
    for i in range(30):
        t = 3.0 + i * 2
        events.append(
            make_event(
                EventType.SEND,
                from_agent="A",
                to_agent="B",
                message_body=b"request",
                timestamp=t,
            )
        )
        events.append(
            make_event(
                EventType.SEND,
                from_agent="B",
                to_agent="A",
                message_body=b"reject",
                timestamp=t + 1,
            )
        )
    return events


@pytest.fixture()
def no_cycle_linear() -> list[Event]:
    """A->B->C->D  (no cycle, linear chain)."""
    return [
        make_event(EventType.REGISTER, from_agent="A", timestamp=1.0),
        make_event(EventType.REGISTER, from_agent="B", timestamp=2.0),
        make_event(EventType.REGISTER, from_agent="C", timestamp=3.0),
        make_event(EventType.REGISTER, from_agent="D", timestamp=4.0),
        make_event(EventType.WAIT_FOR, from_agent="A", to_agent="B", timestamp=5.0),
        make_event(EventType.WAIT_FOR, from_agent="B", to_agent="C", timestamp=6.0),
        make_event(EventType.WAIT_FOR, from_agent="C", to_agent="D", timestamp=7.0),
    ]
