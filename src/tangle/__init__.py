# src/tangle/__init__.py

__version__ = "0.1.0"

import contextlib

from tangle.async_monitor import AsyncTangleMonitor
from tangle.config import TangleConfig
from tangle.logging import configure_logging, shutdown_logging
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
    "__version__",
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
