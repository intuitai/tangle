# src/tangle/__init__.py

import contextlib

from tangle.config import TangleConfig
from tangle.logging import configure_logging, shutdown_logging
from tangle.async_monitor import AsyncTangleMonitor
from tangle.monitor import TangleMonitor

with contextlib.suppress(ImportError):
    from tangle.metrics import TangleMetrics
from tangle.types import (
    AgentID,
    AgentStatus,
    Cycle,
    Detection,
    DetectionType,
    Edge,
    Event,
    EventType,
    LivelockPattern,
    ResolutionAction,
    Severity,
)

__all__ = [
    "TangleConfig",
    "TangleMetrics",
    "AsyncTangleMonitor",
    "TangleMonitor",
    "configure_logging",
    "shutdown_logging",
    "AgentID",
    "AgentStatus",
    "Cycle",
    "Detection",
    "DetectionType",
    "Edge",
    "Event",
    "EventType",
    "LivelockPattern",
    "ResolutionAction",
    "Severity",
]
