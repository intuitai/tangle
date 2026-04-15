# src/tangle/types.py

import enum
import time
from dataclasses import dataclass, field
from uuid import uuid4

AgentID = str  # type alias


class AgentStatus(enum.Enum):
    ACTIVE = "active"
    WAITING = "waiting"
    COMPLETED = "completed"
    CANCELED = "canceled"


class EventType(enum.Enum):
    WAIT_FOR = "wait_for"
    RELEASE = "release"
    SEND = "send"
    REGISTER = "register"
    COMPLETE = "complete"
    CANCEL = "cancel"
    PROGRESS = "progress"


@dataclass(frozen=True, slots=True)
class Event:
    type: EventType
    timestamp: float
    workflow_id: str
    from_agent: AgentID
    to_agent: AgentID = ""
    resource: str = ""
    message_body: bytes = b""
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Edge:
    from_agent: AgentID
    to_agent: AgentID
    resource: str
    created_at: float
    workflow_id: str


class DetectionType(enum.Enum):
    DEADLOCK = "deadlock"
    LIVELOCK = "livelock"


class Severity(enum.Enum):
    WARNING = "warning"
    CRITICAL = "critical"


class ResolutionAction(enum.Enum):
    ALERT = "alert"
    CANCEL_YOUNGEST = "cancel_youngest"
    CANCEL_ALL = "cancel_all"
    TIEBREAKER = "tiebreaker"
    ESCALATE = "escalate"


class ResolutionFailurePolicy(enum.Enum):
    IGNORE = "ignore"
    RAISE = "raise"
    MARK_UNRESOLVED = "mark_unresolved"
    RETRY_WEBHOOK = "retry_webhook"
    RETRY_CHAIN = "retry_chain"


@dataclass(slots=True)
class Cycle:
    id: str = field(default_factory=lambda: str(uuid4()))
    detected_at: float = field(default_factory=time.monotonic)
    agents: list[AgentID] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    workflow_id: str = ""
    resolved: bool = False
    resolution: ResolutionAction | None = None


@dataclass(slots=True)
class LivelockPattern:
    id: str = field(default_factory=lambda: str(uuid4()))
    detected_at: float = field(default_factory=time.monotonic)
    agents: list[AgentID] = field(default_factory=list)
    pattern_length: int = 0
    repeat_count: int = 0
    workflow_id: str = ""
    resolved: bool = False
    resolution: ResolutionAction | None = None


@dataclass(slots=True)
class Detection:
    type: DetectionType
    severity: Severity
    cycle: Cycle | None = None
    livelock: LivelockPattern | None = None
    resolution_exhausted: bool = False
