# Tangle — Developer Design Document

**Agent Workflow Deadlock and Livelock Detection**

Version: 3.0  
Language: Python 3.14  
Package Manager: uv (Astral)  
Primary Integrations: LangGraph (native), OpenTelemetry (language-agnostic)  
Author: Design Team  
Status: Ready for Implementation

---

## 1. Problem statement

Multi-agent AI systems can silently fail in two ways that are invisible to conventional monitoring:

**Deadlock** — Agent A waits for a response from Agent B, which is waiting for Agent A. The workflow hangs indefinitely until a timeout fires minutes later. In production, this manifests as "the workflow just stopped" with no error, no log, and no metric.

**Livelock** — Agents retry each other in a loop: Agent A sends a request to Agent B, B rejects it, A reformulates and retries, B rejects again. The agents are busy (not blocked), so timeouts don't fire and monitoring shows "healthy." This pattern is especially common with LLM agents because the non-deterministic nature of model outputs means the same rejection-retry cycle can repeat with slightly different wording each time, masking the loop.

Tangle is a Python library that monitors agent workflow execution in real time, detects deadlock and livelock conditions, and triggers configurable resolution actions. It integrates natively with LangGraph's state graph model via decorators and also provides a language-agnostic OpenTelemetry ingestion path so that agents written in any language (Go, TypeScript, Java, Rust) can be monitored through standard OTLP spans. It can also run as a standalone FastAPI sidecar.

### 1.1. Goals

- Detect deadlock cycles in agent dependency graphs within 500ms of formation.
- Detect livelock patterns (repetitive retry loops with no forward progress) within a configurable window.
- Native LangGraph integration via decorators and graph hooks — zero boilerplate for LangGraph users.
- **Language-agnostic OTel ingestion** — any traced agent system can feed Tangle via standard OTLP spans with `tangle.*` attributes.
- Configurable resolution: alert, cancel youngest agent, inject tiebreaker prompt, escalate to human.
- Ship as a `pip install tangle-detect` / `uv add tangle-detect` library importable into any Python project.
- Also usable as a standalone FastAPI sidecar for non-Python agent systems.
- All tests runnable in containers with no host-level dependencies.

### 1.2. Non-goals

- Tangle does not orchestrate agents — it observes and intervenes.
- Tangle does not replace LangGraph — it augments it.
- Tangle does not perform distributed consensus — it operates as an in-process or sidecar observer.
- Tangle does not prevent deadlocks by design (deadlock avoidance) — it detects them at runtime.

---

## 2. Name rationale

**Tangle** — when agent dependencies form a cycle, the workflow is literally tangled. The word works as both noun ("we found a tangle") and verb ("the agents tangled"). Short, memorable, and immediately communicates the problem domain.

PyPI package name: `tangle-detect`  
Import name: `tangle`

---

## 3. Architecture overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Ingestion Layer                                │
│                                                                      │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────┐  │
│  │  LangGraph      │  │  OTel / OTLP     │  │  HTTP / gRPC       │  │
│  │  Decorators     │  │  Span Receiver   │  │  Sidecar API       │  │
│  │  (@tangle_node) │  │  (port 4317)     │  │  (port 8090)       │  │
│  │                 │  │                  │  │                    │  │
│  │  Python in-proc │  │  Any language    │  │  Any language      │  │
│  └────────┬────────┘  └────────┬─────────┘  └────────┬───────────┘  │
│           │                    │                      │              │
│           └────────────────────┼──────────────────────┘              │
│                                │                                     │
│                       Unified Event stream                           │
└────────────────────────────────┼─────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         Tangle Core                                  │
│                                                                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────────┐ │
│  │  Wait-For Graph  │  │  Cycle Detector  │  │ Livelock Detector  │ │
│  │  (directed graph │  │  (incremental    │  │ (conversation-     │ │
│  │   of agent deps) │  │   DFS + Kahn's)  │  │  level sliding     │ │
│  │                  │  │                  │  │  window pattern)   │ │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────┬─────────┘ │
│           └─────────────────────┼────────────────────────┘           │
│                                 ▼                                    │
│                      ┌─────────────────────┐                         │
│                      │    Resolver Chain   │                         │
│                      │  alert → cancel →   │                         │
│                      │  tiebreaker →       │                         │
│                      │  escalate           │                         │
│                      └─────────────────────┘                         │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 4. Project setup

### 4.1. Project initialization

```bash
uv init tangle-detect --package
cd tangle-detect
uv python pin 3.14
```

### 4.2. pyproject.toml

```toml
[project]
name = "tangle-detect"
version = "0.1.0"
description = "Agent workflow deadlock and livelock detection"
readme = "README.md"
license = "MIT"
requires-python = ">=3.14"
authors = [
    { name = "Design Team" },
]
keywords = [
    "agents", "langgraph", "deadlock", "livelock",
    "monitoring", "observability", "opentelemetry", "otel", "otlp",
]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.14",
    "Topic :: Software Development :: Libraries",
    "Topic :: System :: Monitoring",
]

dependencies = [
    "xxhash>=3.5.0",
    "pydantic>=2.10",
    "structlog>=24.4",
]

[project.optional-dependencies]
langgraph = [
    "langgraph>=0.3",
    "langchain-core>=0.3",
]
server = [
    "fastapi>=0.115",
    "uvicorn>=0.34",
]
otel = [
    "opentelemetry-api>=1.29",
    "opentelemetry-sdk>=1.29",
    "opentelemetry-exporter-otlp-proto-grpc>=1.29",
]

[project.scripts]
tangle = "tangle.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/tangle"]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.25",
    "pytest-cov>=6.0",
    "pytest-timeout>=2.3",
    "pytest-benchmark>=4.0",
    "hypothesis>=6.120",
    "ruff>=0.8",
    "mypy>=1.14",
    "httpx>=0.28",
    "langgraph>=0.3",
    "langchain-core>=0.3",
    "fastapi>=0.115",
    "uvicorn>=0.34",
    "opentelemetry-api>=1.29",
    "opentelemetry-sdk>=1.29",
    "opentelemetry-exporter-otlp-proto-grpc>=1.29",
    "grpcio>=1.68",
]

[tool.uv]
default-groups = ["dev"]

[tool.ruff]
# NOTE: Update to "py314" when ruff adds support. As of ruff 0.9.x, py313 is the highest.
target-version = "py313"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "TCH"]

[tool.mypy]
# NOTE: Update to "3.14" when mypy adds support. As of mypy 1.14, 3.13 is the highest.
python_version = "3.13"
strict = true
warn_return_any = true
warn_unused_configs = true

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
timeout = 30
addopts = "-ra --strict-markers"
markers = [
    "integration: marks tests that require external services",
    "slow: marks tests that take >5 seconds",
]
```

### 4.3. .python-version

```
3.14
```

---

## 5. Package structure

```
tangle-detect/
├── src/
│   └── tangle/
│       ├── __init__.py              # Public API re-exports
│       ├── py.typed                 # PEP 561 marker
│       ├── monitor.py               # TangleMonitor (main facade)
│       ├── config.py                # Configuration (Pydantic models)
│       ├── types.py                 # Core types: AgentID, Event, Detection, etc.
│       │
│       ├── graph/
│       │   ├── __init__.py
│       │   ├── wfg.py              # WaitForGraph data structure
│       │   └── snapshot.py          # Serializable snapshot (JSON, DOT)
│       │
│       ├── detector/
│       │   ├── __init__.py
│       │   ├── base.py             # Detector protocol
│       │   ├── cycle.py            # Deadlock (cycle) detector
│       │   └── livelock.py         # Livelock (pattern repetition) detector
│       │
│       ├── resolver/
│       │   ├── __init__.py
│       │   ├── base.py             # Resolver protocol
│       │   ├── alert.py            # Alert resolver (log + callback)
│       │   ├── cancel.py           # Cancel resolver
│       │   ├── tiebreaker.py       # Tiebreaker prompt injector
│       │   ├── escalate.py         # Webhook escalation
│       │   └── chain.py            # Chain of resolvers
│       │
│       ├── integrations/
│       │   ├── __init__.py
│       │   ├── langgraph.py        # LangGraph decorators and hooks
│       │   └── otel.py             # OpenTelemetry OTLP span receiver + parser
│       │
│       ├── server/
│       │   ├── __init__.py
│       │   ├── app.py              # FastAPI application
│       │   └── routes.py           # HTTP/REST endpoints
│       │
│       ├── store/
│       │   ├── __init__.py
│       │   ├── base.py             # Store protocol
│       │   ├── memory.py           # In-memory store
│       │   └── sqlite.py           # SQLite persistent store
│       │
│       └── cli.py                  # CLI entrypoint for sidecar mode
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                  # Shared fixtures (FakeClock, scenarios, etc.)
│   ├── test_types.py
│   ├── test_config.py
│   │
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── test_wfg.py
│   │   └── test_snapshot.py
│   │
│   ├── detector/
│   │   ├── __init__.py
│   │   ├── test_cycle.py
│   │   └── test_livelock.py
│   │
│   ├── resolver/
│   │   ├── __init__.py
│   │   └── test_resolvers.py
│   │
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── test_langgraph.py
│   │   └── test_otel.py
│   │
│   ├── server/
│   │   ├── __init__.py
│   │   └── test_routes.py
│   │
│   ├── store/
│   │   ├── __init__.py
│   │   ├── test_memory.py
│   │   ├── test_sqlite.py
│   │   └── conformance.py          # Shared conformance suite
│   │
│   ├── benchmarks/
│   │   ├── __init__.py
│   │   └── bench_cycle.py
│   │
│   └── integration/
│       ├── __init__.py
│       ├── test_langgraph_e2e.py   # Full LangGraph deadlock/livelock scenario
│       ├── test_otel_e2e.py        # OTLP spans → detection end-to-end
│       ├── test_server_e2e.py      # FastAPI sidecar end-to-end
│       └── test_resolution_e2e.py  # Detect → Resolve → Verify cycle
│
├── pyproject.toml
├── .python-version
├── uv.lock
├── Makefile
├── Dockerfile
├── Dockerfile.test
├── docker-compose.yml
└── README.md
```

---

## 6. Core types

```python
# src/tangle/types.py
#
# NOTE: Python 3.14 has PEP 649 (deferred annotation evaluation) built in.
# Do NOT use `from __future__ import annotations` — it converts annotations
# to strings which breaks Pydantic V2 runtime validation.

import enum
import time
from dataclasses import dataclass, field
from uuid import uuid4

AgentID = str  # type alias — e.g., "researcher", "writer", "reviewer"


class AgentStatus(enum.Enum):
    """Internal status of an agent within Tangle's Wait-For Graph.

    Named AgentStatus (not AgentState) to avoid collision with LangGraph's
    user-defined AgentState TypedDict, which is commonly used in LangGraph projects.
    """
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
    """An immutable event emitted by an agent or ingestion adapter.

    NOTE: `frozen=True` prevents reassignment of fields but does NOT prevent
    mutation of the `metadata` dict contents. Treat metadata as read-only
    after construction. For true immutability, use tuple[tuple[str,str], ...]
    instead, but dict is kept here for ergonomics.
    """
    type: EventType
    timestamp: float  # time.monotonic() or injected clock
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
```

---

## 7. Configuration

```python
# src/tangle/config.py

from pydantic import BaseModel, Field
from tangle.types import ResolutionAction


class TangleConfig(BaseModel):
    """Configuration for the Tangle monitor."""

    model_config = {"extra": "forbid", "use_enum_values": True}
    # use_enum_values=True allows passing resolution="alert" (string)
    # in addition to resolution=ResolutionAction.ALERT (enum).

    # Deadlock detection
    cycle_check_interval: float = Field(default=5.0, description="Seconds between periodic full-graph scans")
    max_cycle_length: int = Field(default=20, ge=2, description="Maximum cycle length to search for")

    # Livelock detection
    livelock_window: int = Field(default=50, ge=4, description="Number of recent messages to analyze")
    livelock_min_repeats: int = Field(default=3, ge=2, description="Minimum pattern repetitions to trigger")
    livelock_min_pattern: int = Field(default=2, ge=1, description="Minimum messages per pattern iteration")
    livelock_ring_size: int = Field(default=200, ge=10, description="Ring buffer capacity per agent pair")
    livelock_semantic: bool = Field(default=False, description="Enable semantic hashing for rephrased messages")

    # Resolution
    resolution: ResolutionAction = Field(default=ResolutionAction.ALERT)
    escalation_webhook_url: str = Field(default="")
    tiebreaker_prompt: str = Field(
        default="You appear to be in a loop. Please try a different approach or report that you are stuck."
    )

    # Event processing
    event_queue_size: int = Field(default=10_000, ge=100)

    # Store
    store_backend: str = Field(default="memory", pattern="^(memory|sqlite)$")
    sqlite_path: str = Field(default="tangle.db")

    # OTel ingestion
    otel_enabled: bool = Field(default=False, description="Enable OTLP gRPC span receiver")
    otel_port: int = Field(default=4317, description="OTLP gRPC receiver port")

    # Server (sidecar mode)
    server_host: str = Field(default="0.0.0.0")
    server_port: int = Field(default=8090)
```

---

## 8. Core algorithm: deadlock detection

### 8.1. Wait-For Graph

```python
# src/tangle/graph/wfg.py

import threading
from collections import defaultdict
from tangle.types import AgentID, AgentStatus, Edge

class WaitForGraph:
    """Thread-safe directed graph tracking agent blocking dependencies."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._edges: dict[AgentID, dict[AgentID, Edge]] = defaultdict(dict)
        self._states: dict[AgentID, AgentStatus] = {}
        self._join_times: dict[AgentID, float] = {}
        self._workflow_map: dict[AgentID, str] = {}  # agent → workflow_id

    def add_edge(self, edge: Edge) -> None: ...
    def remove_edge(self, from_agent: AgentID, to_agent: AgentID) -> None: ...
    def register_agent(self, agent_id: AgentID, workflow_id: str, timestamp: float) -> None: ...
    def set_state(self, agent_id: AgentID, state: AgentStatus) -> None: ...
    def has_edge(self, from_agent: AgentID, to_agent: AgentID) -> bool: ...
    def outgoing(self, agent_id: AgentID) -> list[Edge]: ...
    def all_edges(self) -> list[Edge]: ...
    def all_nodes(self) -> list[AgentID]: ...
    def get_join_time(self, agent_id: AgentID) -> float | None: ...
    def edge_count(self) -> int: ...
    def node_count(self) -> int: ...
    def agents_in_workflow(self, workflow_id: str) -> list[AgentID]: ...
    def clear_workflow(self, workflow_id: str) -> None: ...
    def snapshot(self) -> "GraphSnapshot": ...
```

### 8.2. Incremental cycle detection

```python
# src/tangle/detector/cycle.py

class CycleDetector:
    """Detects cycles (deadlocks) in the Wait-For Graph."""

    def __init__(self, graph: WaitForGraph, max_depth: int = 20) -> None: ...

    def on_edge_added(self, edge: Edge) -> Cycle | None:
        """
        Called when a new WaitFor edge is added.
        Runs incremental DFS from edge.to_agent looking for edge.from_agent.
        If found, a cycle exists. Returns the Cycle or None.
        """
        ...

    def full_scan(self) -> list[Cycle]:
        """
        Kahn's algorithm on the full graph.
        Uses graph.all_edges() and graph.all_nodes() to compute in-degrees.
        Any nodes not in the topological order are in a cycle.
        Traces exact cycle paths via DFS.
        """
        ...
```

**Incremental algorithm** (called on every `WaitFor` event):

```
on_edge_added(edge: Edge(from=A, to=B)):

1. Add edge A → B to WFG.

2. DFS from B following outgoing edges, looking for A.
   - Depth limit: max_cycle_length.
   - If A is reachable from B: cycle found.
   - Path from B to A + edge A→B = the cycle.

3. Return Cycle or None.
```

**Periodic full scan** (every `cycle_check_interval` seconds):

```
full_scan():

1. Snapshot via graph.all_edges() and graph.all_nodes().

2. Kahn's algorithm:
   a. Build in-degree map from all_edges().
   b. Queue all nodes with in-degree 0.
   c. Process queue: dequeue N, for each N→M decrement M's in-degree.
      If M reaches 0, enqueue M.
   d. Remaining nodes are in cycles.

3. For each cycle-set, trace exact path via DFS.

4. Return list of Cycles.
```

---

## 9. Core algorithm: livelock detection

The livelock detector uses **two levels of buffering** to catch both single-direction and bidirectional patterns:

1. **Per-pair buffers** `(from, to)` — catches single-direction repetition (e.g., agent A sending the same request to B repeatedly).
2. **Per-workflow conversation buffer** — interleaves all messages in a workflow chronologically. Catches bidirectional ping-pong (e.g., A sends "request", B sends "reject", A sends "request", B sends "reject" ...), which is the most common livelock pattern.

```python
# src/tangle/detector/livelock.py

import xxhash
from tangle.types import AgentID, LivelockPattern

class RingBuffer:
    """Fixed-capacity circular buffer for message digests."""

    def __init__(self, capacity: int = 200) -> None: ...
    def append(self, digest: bytes) -> None: ...
    def last_n(self, n: int) -> list[bytes]: ...
    def __len__(self) -> int: ...


class LivelockDetector:
    """Detects repetitive message patterns between agent pairs and within workflows."""

    def __init__(
        self,
        window: int = 50,
        min_repeats: int = 3,
        min_pattern: int = 2,
        ring_size: int = 200,
    ) -> None:
        # Per-pair buffers: key = (from, to)
        self._pair_buffers: dict[tuple[AgentID, AgentID], RingBuffer] = {}
        # Per-workflow conversation buffers: key = workflow_id
        # Interleaves all (from, to, hash) as a single digest per message
        self._conversation_buffers: dict[str, RingBuffer] = {}
        ...

    def on_message(
        self,
        from_agent: AgentID,
        to_agent: AgentID,
        body: bytes,
        workflow_id: str,
    ) -> LivelockPattern | None:
        """
        1. Compute content_hash = xxhash.xxh128(body).digest() (16 bytes).
        2. Compute conversation_hash = xxhash.xxh128(from + to + content_hash).digest().
           This captures WHO sent WHAT TO WHOM as a single digest.
        3. Append content_hash to per-pair buffer for (from, to).
        4. Append conversation_hash to per-workflow conversation buffer.
        5. Run pattern detection on BOTH buffers:
           a. Per-pair: checks for single-direction repetition (e.g., same request resent).
           b. Conversation: checks for bidirectional ping-pong patterns.
           For each buffer, scan the last `window` digests:
             - For pattern_length P from min_pattern to window // min_repeats:
               - Extract last P digests as candidate pattern.
               - Count consecutive repeats scanning backward.
               - If repeats >= min_repeats → livelock detected.
        6. Return the first LivelockPattern found, or None.
        """
        ...

    def report_progress(self, workflow_id: str) -> None:
        """Reset repeat counters for all buffers in this workflow."""
        ...

    def clear_workflow(self, workflow_id: str) -> None:
        """Remove all buffers (pair and conversation) for a workflow."""
        ...
```

---

## 10. LangGraph integration

This is the primary in-process integration — designed so LangGraph users can add Tangle with minimal code changes.

### 10.1. Decorator-based instrumentation

```python
# src/tangle/integrations/langgraph.py

from functools import wraps
from typing import Any, Callable
import xxhash

from tangle.monitor import TangleMonitor
from tangle.types import AgentID, EventType, Event


def _compute_state_keys_hash(state: dict[str, Any], exclude: set[str]) -> bytes:
    """Hash the non-Tangle state keys to detect state changes."""
    relevant = {k: repr(v) for k, v in sorted(state.items()) if k not in exclude}
    return xxhash.xxh128(str(relevant).encode()).digest()


def _diff_keys(old: dict[str, Any], new: dict[str, Any]) -> set[str]:
    """Return the set of keys whose values changed between old and new."""
    changed = set()
    for k in new:
        if k not in old or old[k] != new[k]:
            changed.add(k)
    return changed


_TANGLE_KEYS = {"tangle_workflow_id"}


def tangle_node(monitor: TangleMonitor, agent_id: AgentID):
    """
    Decorator that instruments a LangGraph node function.

    Wraps the node so that:
    - On entry: emits a Register event (if first call).
    - On exit: emits Send events for each state key changed by this node
      (hashing the delta for livelock detection), then emits a Complete event.
    - On exception: emits a Cancel event, then re-raises.

    Usage:
        @tangle_node(monitor, agent_id="researcher")
        def researcher_node(state: AgentState) -> AgentState:
            ...
    """
    _registered: set[str] = set()  # tracks (workflow_id, agent_id) pairs

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(state: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
            workflow_id = state.get("tangle_workflow_id", "default")
            reg_key = f"{workflow_id}:{agent_id}"

            # Register on first call per workflow
            if reg_key not in _registered:
                monitor.process_event(Event(
                    type=EventType.REGISTER,
                    timestamp=monitor.clock(),
                    workflow_id=workflow_id,
                    from_agent=agent_id,
                ))
                _registered.add(reg_key)

            try:
                result = fn(state, *args, **kwargs)
            except Exception:
                monitor.process_event(Event(
                    type=EventType.CANCEL,
                    timestamp=monitor.clock(),
                    workflow_id=workflow_id,
                    from_agent=agent_id,
                ))
                raise

            # Emit Send events for each state key this node changed.
            # The "to_agent" is the next node that will consume this key.
            # Since we don't know the graph topology at decoration time,
            # we use a synthetic target: the key name itself as a resource ID.
            if isinstance(result, dict):
                for key in result:
                    if key not in _TANGLE_KEYS:
                        body = xxhash.xxh128(
                            f"{key}={repr(result[key])}".encode()
                        ).digest()
                        monitor.process_event(Event(
                            type=EventType.SEND,
                            timestamp=monitor.clock(),
                            workflow_id=workflow_id,
                            from_agent=agent_id,
                            to_agent="__graph__",  # synthetic target
                            resource=key,
                            message_body=body,
                        ))

            return result
        return wrapper
    return decorator


def tangle_conditional_edge(monitor: TangleMonitor, from_agent: AgentID):
    """
    Decorator for LangGraph conditional edge functions.
    Records the routing decision as a WaitFor edge from the
    source agent to the selected destination agent, and a
    corresponding Release when the destination completes.
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(state: dict[str, Any], *args: Any, **kwargs: Any) -> str:
            result = fn(state, *args, **kwargs)
            workflow_id = state.get("tangle_workflow_id", "default")

            if result and result != "__end__":
                monitor.process_event(Event(
                    type=EventType.WAIT_FOR,
                    timestamp=monitor.clock(),
                    workflow_id=workflow_id,
                    from_agent=from_agent,
                    to_agent=result,
                    resource="conditional_edge",
                ))

            return result
        return wrapper
    return decorator
```

### 10.2. Usage example with LangGraph

```python
from typing import TypedDict
from langgraph.graph import StateGraph, END
from tangle import TangleMonitor, TangleConfig
from tangle.integrations.langgraph import tangle_node, tangle_conditional_edge
from tangle.types import ResolutionAction

# --- Setup ---
monitor = TangleMonitor(TangleConfig(resolution=ResolutionAction.ALERT))


# --- State ---
class WorkflowState(TypedDict):
    tangle_workflow_id: str
    research: str
    draft: str
    feedback: str
    iteration: int


# --- Nodes ---
@tangle_node(monitor, agent_id="researcher")
def researcher(state: WorkflowState) -> WorkflowState:
    return {"research": f"Research findings v{state.get('iteration', 1)}"}


@tangle_node(monitor, agent_id="writer")
def writer(state: WorkflowState) -> WorkflowState:
    return {"draft": f"Draft based on: {state['research']}"}


@tangle_node(monitor, agent_id="reviewer")
def reviewer(state: WorkflowState) -> WorkflowState:
    return {"feedback": "Needs more citations", "iteration": state.get("iteration", 0) + 1}


# --- Routing ---
@tangle_conditional_edge(monitor, from_agent="reviewer")
def should_continue(state: WorkflowState) -> str:
    if state.get("iteration", 0) >= 5:
        return END
    return "researcher"  # sends back for revision — potential livelock!


# --- Graph ---
graph = StateGraph(WorkflowState)
graph.add_node("researcher", researcher)
graph.add_node("writer", writer)
graph.add_node("reviewer", reviewer)

graph.set_entry_point("researcher")
graph.add_edge("researcher", "writer")
graph.add_edge("writer", "reviewer")
graph.add_conditional_edges("reviewer", should_continue)

app = graph.compile()

# --- Run ---
result = app.invoke({
    "tangle_workflow_id": "wf-001",
    "research": "",
    "draft": "",
    "feedback": "",
    "iteration": 0,
})

# Check for detections
for d in monitor.active_detections():
    print(f"DETECTED: {d.type.value} involving "
          f"{d.cycle.agents if d.cycle else d.livelock.agents}")
```

### 10.3. Explicit SDK hooks (for advanced use)

```python
monitor.wait_for(workflow_id="wf-001", from_agent="researcher", to_agent="writer", resource="draft")
monitor.release(workflow_id="wf-001", from_agent="researcher", to_agent="writer")
monitor.send(workflow_id="wf-001", from_agent="writer", to_agent="reviewer", body=b"draft content")
monitor.report_progress(workflow_id="wf-001", description="completed step 3")
monitor.complete(workflow_id="wf-001", agent_id="researcher")
```

---

## 11. OTel ingestion — language-agnostic integration

This integration enables any agent system (Go, TypeScript, Java, Rust, etc.) to feed Tangle by emitting standard OpenTelemetry spans with Tangle-specific attributes. **No Tangle SDK required in the agent's language.**

### 11.1. Span attribute convention

Agents emit standard OTel spans with these semantic attributes:

| Attribute | Type | Required | Description |
|---|---|---|---|
| `tangle.agent.id` | string | yes | The agent that created the span |
| `tangle.workflow.id` | string | yes | The workflow run ID |
| `tangle.event.type` | string | yes | One of: `wait_for`, `release`, `send`, `register`, `complete`, `cancel`, `progress` |
| `tangle.target.agent` | string | for wait_for/release/send | The target agent ID |
| `tangle.resource` | string | no | What is being waited for or sent |
| `tangle.message.hash` | string | for send | xxHash hex digest of the message body |

### 11.2. Span-to-Event mapping

The OTel collector parses incoming OTLP spans as follows:

```python
# src/tangle/integrations/otel.py

from opentelemetry.proto.collector.trace.v1 import (
    trace_service_pb2,
    trace_service_pb2_grpc,
)
from tangle.types import Event, EventType

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


def parse_span_to_event(span) -> Event | None:
    """
    Extract a Tangle Event from an OTel span.

    Returns None if the span does not have the required tangle.* attributes
    (i.e., it is a non-Tangle span and should be ignored).
    """
    attrs = _extract_attributes(span)

    agent_id = attrs.get(_ATTR_AGENT)
    workflow_id = attrs.get(_ATTR_WORKFLOW)
    event_type_str = attrs.get(_ATTR_EVENT_TYPE)

    # All three are required; skip spans without them
    if not agent_id or not workflow_id or not event_type_str:
        return None

    event_type = _EVENT_TYPE_MAP.get(event_type_str)
    if event_type is None:
        return None  # unknown event type, skip

    return Event(
        type=event_type,
        timestamp=span.start_time_unix_nano / 1e9,
        workflow_id=workflow_id,
        from_agent=agent_id,
        to_agent=attrs.get(_ATTR_TARGET, ""),
        resource=attrs.get(_ATTR_RESOURCE, ""),
        message_body=bytes.fromhex(attrs.get(_ATTR_MSG_HASH, "")),
    )
```

### 11.3. OTLP gRPC receiver

```python
# src/tangle/integrations/otel.py (continued)

import grpc
from concurrent import futures
import structlog

logger = structlog.get_logger("tangle.otel")


class TangleTraceServicer(trace_service_pb2_grpc.TraceServiceServicer):
    """gRPC servicer that receives OTLP spans and feeds them to the monitor."""

    def __init__(self, monitor: "TangleMonitor") -> None:
        self._monitor = monitor

    def Export(self, request, context):
        for resource_spans in request.resource_spans:
            for scope_spans in resource_spans.scope_spans:
                for span in scope_spans.spans:
                    event = parse_span_to_event(span)
                    if event is not None:
                        self._monitor.process_event(event)
                    # Non-Tangle spans are silently ignored
        return trace_service_pb2.ExportTraceServiceResponse()


class OTelCollector:
    """Standalone OTLP gRPC receiver. Runs in a background thread."""

    def __init__(self, monitor: "TangleMonitor", port: int = 4317) -> None:
        self._monitor = monitor
        self._port = port
        self._server: grpc.Server | None = None

    def start(self) -> None:
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        trace_service_pb2_grpc.add_TraceServiceServicer_to_server(
            TangleTraceServicer(self._monitor), self._server,
        )
        self._server.add_insecure_port(f"[::]:{self._port}")
        self._server.start()
        logger.info("otel_collector_started", port=self._port)

    def stop(self, grace: float = 5.0) -> None:
        if self._server:
            self._server.stop(grace=grace)
            logger.info("otel_collector_stopped")
```

### 11.4. Usage example — TypeScript agent sending spans to Tangle

```typescript
// Any OTel-instrumented agent can emit Tangle spans.
// This example uses the OpenTelemetry JS SDK.

import { trace } from '@opentelemetry/api';

const tracer = trace.getTracer('my-agent');

function waitForAgent(workflowId: string, fromAgent: string, toAgent: string, resource: string) {
  const span = tracer.startSpan('tangle.wait_for');
  span.setAttribute('tangle.agent.id', fromAgent);
  span.setAttribute('tangle.workflow.id', workflowId);
  span.setAttribute('tangle.event.type', 'wait_for');
  span.setAttribute('tangle.target.agent', toAgent);
  span.setAttribute('tangle.resource', resource);
  span.end();
}

// Agent "researcher" waits for "writer" to produce a draft
waitForAgent('wf-001', 'researcher', 'writer', 'draft');
```

The OTel exporter is configured to send spans to Tangle's OTLP endpoint (`http://tangle-sidecar:4317`).

---

## 12. TangleMonitor — main facade

```python
# src/tangle/monitor.py

import threading
from collections.abc import Callable

from tangle.config import TangleConfig
from tangle.types import AgentID, Detection, Event, EventType
from tangle.graph.wfg import WaitForGraph
from tangle.graph.snapshot import GraphSnapshot
from tangle.detector.cycle import CycleDetector
from tangle.detector.livelock import LivelockDetector
from tangle.resolver.chain import ResolverChain


class TangleMonitor:
    """Main entry point. Thread-safe. Can be used from sync or async code."""

    def __init__(
        self,
        config: TangleConfig | None = None,
        clock: Callable[[], float] | None = None,
        on_detection: Callable[[Detection], None] | None = None,
        cancel_fn: Callable[[AgentID, str], None] | None = None,
        tiebreaker_fn: Callable[[AgentID, str], None] | None = None,
        escalate_fn: Callable[[Detection], None] | None = None,
    ) -> None: ...

    # --- SDK hooks (convenience wrappers around process_event) ---
    def wait_for(self, workflow_id: str, from_agent: AgentID, to_agent: AgentID, resource: str = "") -> None: ...
    def release(self, workflow_id: str, from_agent: AgentID, to_agent: AgentID) -> None: ...
    def send(self, workflow_id: str, from_agent: AgentID, to_agent: AgentID, body: bytes = b"") -> None: ...
    def register(self, workflow_id: str, agent_id: AgentID) -> None: ...
    def complete(self, workflow_id: str, agent_id: AgentID) -> None: ...
    def cancel(self, workflow_id: str, agent_id: AgentID, reason: str = "") -> None: ...
    def report_progress(self, workflow_id: str, description: str = "") -> None: ...

    # --- Core ---
    def process_event(self, event: Event) -> Detection | None:
        """Process a single event. Thread-safe."""
        ...

    # --- Inspection ---
    def snapshot(self, workflow_id: str | None = None) -> GraphSnapshot: ...
    def active_detections(self) -> list[Detection]: ...
    def stats(self) -> dict[str, int]: ...

    # --- Lifecycle ---
    def start_background(self) -> None:
        """Start periodic full-graph scan and optional OTel collector in background threads."""
        ...

    def stop(self) -> None: ...
    def reset_workflow(self, workflow_id: str) -> None: ...
    def clock(self) -> float: ...

    def __enter__(self) -> "TangleMonitor": ...
    def __exit__(self, *args: object) -> None: ...
```

---

## 13. Resolvers

```python
# src/tangle/resolver/base.py

from typing import Protocol
from tangle.types import Detection


class Resolver(Protocol):
    @property
    def name(self) -> str: ...

    def resolve(self, detection: Detection) -> None: ...
```

| Class | Module | Behavior |
|---|---|---|
| `AlertResolver` | `resolver/alert.py` | Logs via structlog, calls `on_detection` callback |
| `CancelResolver` | `resolver/cancel.py` | Calls `cancel_fn(agent_id, reason)` for youngest (or all) agents in cycle |
| `TiebreakerResolver` | `resolver/tiebreaker.py` | Calls `tiebreaker_fn(agent_id, prompt)` to inject a tiebreaker |
| `EscalateResolver` | `resolver/escalate.py` | POSTs to `escalation_webhook_url` with detection JSON |
| `ChainResolver` | `resolver/chain.py` | Tries resolvers in order; stops on first success |

---

## 14. FastAPI sidecar server

```python
# src/tangle/server/app.py

from fastapi import FastAPI
from tangle.monitor import TangleMonitor
from tangle.server.routes import router


def create_app(monitor: TangleMonitor) -> FastAPI:
    app = FastAPI(title="Tangle", version="0.1.0")
    app.state.monitor = monitor
    app.include_router(router, prefix="/v1")
    return app
```

### 14.1. Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/events` | Submit a single event |
| `POST` | `/v1/events/batch` | Submit multiple events |
| `GET` | `/v1/graph/{workflow_id}` | Current WFG as JSON |
| `GET` | `/v1/detections` | List recent detections |
| `GET` | `/v1/stats` | Aggregate statistics |
| `GET` | `/healthz` | Health check |

---

## 15. Observability

### 15.1. Structured logging

Tangle uses `structlog` with bound context:

```python
import structlog
logger = structlog.get_logger("tangle")
```

### 15.2. Metrics callback

```python
monitor = TangleMonitor(
    config=config,
    on_detection=lambda d: prometheus_counter.labels(type=d.type.value).inc(),
)
```

---

## 16. Thread safety and concurrency

- `WaitForGraph` uses `threading.RLock` for all mutations and reads.
- `RingBuffer` uses a per-instance `threading.Lock` (one per agent pair, minimal contention).
- `TangleMonitor.process_event()` is thread-safe — it acquires the WFG lock and detector locks as needed.
- Background periodic scan runs in a `threading.Thread` (daemon=True) started by `start_background()`.
- The OTel collector runs a gRPC server in a `ThreadPoolExecutor` with 4 workers, each calling `process_event()`.
- The FastAPI sidecar processes events asynchronously via `asyncio` but delegates to the thread-safe `process_event()`.
- LangGraph runs nodes synchronously by default; the decorator wraps sync functions and calls `process_event()` directly.

---

## 17. Error handling

- **Event queue full**: If the internal deque exceeds `event_queue_size`, oldest events are dropped with a `structlog.warning`.
- **Resolution failure**: If a resolver raises an exception, the detection stays in `active_detections` and is retried on the next periodic check. The failure is logged.
- **Webhook timeout**: The escalation resolver uses `httpx` with a 10-second timeout. On timeout, it logs and returns, allowing the chain to try the next resolver.
- **LangGraph node exception**: The `tangle_node` decorator catches exceptions, emits a `Cancel` event, then re-raises.
- **OTel malformed spans**: Spans without required `tangle.*` attributes are silently skipped. Spans with unrecognized `tangle.event.type` are logged at DEBUG and skipped.
- **OTel collector failure**: If the gRPC server fails to bind the port, `start_background()` raises `OTelCollectorError`. If a span handler raises, the error is caught per-span; other spans in the batch are still processed.

---

## 18. Testing strategy

### 18.1. Shared fixtures (`tests/conftest.py`)

```python
# tests/conftest.py

import pytest
from tangle.types import (
    AgentID, Detection, DetectionType, Edge, Event, EventType,
    LivelockPattern, Cycle, Severity,
)
from tangle.monitor import TangleMonitor
from tangle.config import TangleConfig


class FakeClock:
    """Controllable clock for deterministic tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def monitor(fake_clock: FakeClock) -> TangleMonitor:
    return TangleMonitor(
        config=TangleConfig(cycle_check_interval=999),  # disable periodic scan in unit tests
        clock=fake_clock,
    )


class MockResolver:
    """Records all detections for assertion."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.detections: list[Detection] = []
        self.should_fail = should_fail

    @property
    def name(self) -> str:
        return "mock"

    def resolve(self, detection: Detection) -> None:
        self.detections.append(detection)
        if self.should_fail:
            raise RuntimeError("mock resolver failure")

    @property
    def count(self) -> int:
        return len(self.detections)

    @property
    def last(self) -> Detection | None:
        return self.detections[-1] if self.detections else None


@pytest.fixture
def mock_resolver() -> MockResolver:
    return MockResolver()


# --- Factory helpers ---

def make_event(
    event_type: EventType = EventType.REGISTER,
    workflow_id: str = "wf-test",
    from_agent: str = "A",
    to_agent: str = "",
    resource: str = "",
    timestamp: float = 0.0,
) -> Event:
    return Event(
        type=event_type, timestamp=timestamp, workflow_id=workflow_id,
        from_agent=from_agent, to_agent=to_agent, resource=resource,
    )


def make_detection(
    dtype: DetectionType = DetectionType.DEADLOCK,
    workflow_id: str = "wf-test",
) -> Detection:
    if dtype == DetectionType.DEADLOCK:
        return Detection(
            type=dtype, severity=Severity.CRITICAL,
            cycle=Cycle(workflow_id=workflow_id, agents=["A", "B"]),
        )
    return Detection(
        type=dtype, severity=Severity.CRITICAL,
        livelock=LivelockPattern(workflow_id=workflow_id, agents=["A", "B"],
                                  pattern_length=2, repeat_count=3),
    )


# --- Pre-built event scenarios ---

def deadlock_2(wf: str = "wf-test") -> list[Event]:
    """A→B, B→A deadlock."""
    return [
        Event(type=EventType.REGISTER, timestamp=0, workflow_id=wf, from_agent="A"),
        Event(type=EventType.REGISTER, timestamp=0, workflow_id=wf, from_agent="B"),
        Event(type=EventType.WAIT_FOR, timestamp=1, workflow_id=wf, from_agent="A", to_agent="B", resource="x"),
        Event(type=EventType.WAIT_FOR, timestamp=2, workflow_id=wf, from_agent="B", to_agent="A", resource="y"),
    ]


def deadlock_3(wf: str = "wf-test") -> list[Event]:
    """A→B, B→C, C→A deadlock."""
    return [
        Event(type=EventType.REGISTER, timestamp=0, workflow_id=wf, from_agent="A"),
        Event(type=EventType.REGISTER, timestamp=0, workflow_id=wf, from_agent="B"),
        Event(type=EventType.REGISTER, timestamp=0, workflow_id=wf, from_agent="C"),
        Event(type=EventType.WAIT_FOR, timestamp=1, workflow_id=wf, from_agent="A", to_agent="B"),
        Event(type=EventType.WAIT_FOR, timestamp=2, workflow_id=wf, from_agent="B", to_agent="C"),
        Event(type=EventType.WAIT_FOR, timestamp=3, workflow_id=wf, from_agent="C", to_agent="A"),
    ]


def livelock_pingpong(wf: str = "wf-test", repeats: int = 5) -> list[Event]:
    """Bidirectional ping-pong: A→B 'request', B→A 'reject', repeated `repeats` times."""
    events: list[Event] = [
        Event(type=EventType.REGISTER, timestamp=0, workflow_id=wf, from_agent="A"),
        Event(type=EventType.REGISTER, timestamp=0, workflow_id=wf, from_agent="B"),
    ]
    for i in range(repeats):
        events.append(Event(
            type=EventType.SEND, timestamp=i * 2 + 1, workflow_id=wf,
            from_agent="A", to_agent="B", message_body=b"request-payload",
        ))
        events.append(Event(
            type=EventType.SEND, timestamp=i * 2 + 2, workflow_id=wf,
            from_agent="B", to_agent="A", message_body=b"rejection-payload",
        ))
    return events


def no_cycle_linear(wf: str = "wf-test") -> list[Event]:
    """A→B→C→D — no cycle."""
    return [
        Event(type=EventType.REGISTER, timestamp=0, workflow_id=wf, from_agent="A"),
        Event(type=EventType.REGISTER, timestamp=0, workflow_id=wf, from_agent="B"),
        Event(type=EventType.REGISTER, timestamp=0, workflow_id=wf, from_agent="C"),
        Event(type=EventType.REGISTER, timestamp=0, workflow_id=wf, from_agent="D"),
        Event(type=EventType.WAIT_FOR, timestamp=1, workflow_id=wf, from_agent="A", to_agent="B"),
        Event(type=EventType.WAIT_FOR, timestamp=2, workflow_id=wf, from_agent="B", to_agent="C"),
        Event(type=EventType.WAIT_FOR, timestamp=3, workflow_id=wf, from_agent="C", to_agent="D"),
    ]
```

### 18.2. Store conformance suite

```python
# tests/store/conformance.py

from tests.conftest import make_detection, make_event
from tangle.types import DetectionType


def run_store_conformance(store_factory):
    """Run the full conformance suite against any Store implementation."""

    def test_record_and_list_detections():
        store = store_factory()
        detection = make_detection(workflow_id="wf-1")
        store.record_detection(detection)
        results = store.list_detections("wf-1", limit=10)
        assert len(results) == 1
        assert results[0].type == detection.type

    def test_record_event_and_retrieve():
        store = store_factory()
        event = make_event()
        store.record_event(event)
        events = store.get_workflow_events(event.workflow_id)
        assert len(events) == 1

    def test_list_detections_empty():
        store = store_factory()
        assert store.list_detections("nonexistent", limit=10) == []

    def test_list_detections_by_type():
        store = store_factory()
        store.record_detection(make_detection(dtype=DetectionType.DEADLOCK))
        store.record_detection(make_detection(dtype=DetectionType.LIVELOCK))
        deadlocks = store.list_detections_by_type(DetectionType.DEADLOCK, limit=10)
        assert len(deadlocks) == 1

    def test_stats():
        store = store_factory()
        store.record_detection(make_detection(dtype=DetectionType.DEADLOCK))
        store.record_detection(make_detection(dtype=DetectionType.LIVELOCK))
        stats = store.stats()
        assert stats["total_detections"] == 2
        assert stats["deadlocks_detected"] == 1
        assert stats["livelocks_detected"] == 1

    def test_close_is_idempotent():
        store = store_factory()
        store.close()
        store.close()  # should not raise

    for fn in [test_record_and_list_detections, test_record_event_and_retrieve,
               test_list_detections_empty, test_list_detections_by_type,
               test_stats, test_close_is_idempotent]:
        fn()
```

### 18.3. Unit tests — per-module

#### `tests/test_types.py`

| Test | Description |
|---|---|
| `test_event_frozen` | Assigning to `event.type` raises `FrozenInstanceError` |
| `test_event_metadata_mutable_caveat` | Mutating `event.metadata` dict works (documented caveat) |
| `test_event_default_fields` | `to_agent`, `resource`, `message_body`, `metadata` have correct defaults |
| `test_agent_status_values` | All enum values match expected strings |
| `test_event_type_values` | All enum values match expected strings |
| `test_resolution_action_values` | All enum values match expected strings |
| `test_cycle_auto_id` | `Cycle()` generates a unique UUID |
| `test_detection_requires_type_and_severity` | TypeError if constructed without required fields |

#### `tests/test_config.py`

| Test | Description |
|---|---|
| `test_defaults` | Default config has expected values for all fields |
| `test_resolution_accepts_string` | `TangleConfig(resolution="alert")` coerces to `ResolutionAction.ALERT` |
| `test_resolution_accepts_enum` | `TangleConfig(resolution=ResolutionAction.ALERT)` works |
| `test_invalid_resolution_string` | `TangleConfig(resolution="invalid")` raises `ValidationError` |
| `test_extra_fields_forbidden` | `TangleConfig(unknown_field=1)` raises `ValidationError` |
| `test_min_cycle_length` | `max_cycle_length=1` raises `ValidationError` (ge=2) |
| `test_min_livelock_window` | `livelock_window=2` raises `ValidationError` (ge=4) |
| `test_store_backend_validation` | `store_backend="postgres"` raises `ValidationError` |
| `test_store_backend_memory` | `store_backend="memory"` accepted |
| `test_store_backend_sqlite` | `store_backend="sqlite"` accepted |

#### `tests/graph/test_wfg.py`

| Test | Description |
|---|---|
| `test_add_edge` | Add edge, verify adjacency list |
| `test_add_edge_duplicate_idempotent` | Same edge twice → only one stored |
| `test_remove_edge` | Remove edge, verify gone |
| `test_remove_edge_not_found` | Remove non-existent → no error |
| `test_register_agent` | Register sets state to ACTIVE |
| `test_set_state` | State transitions: ACTIVE → WAITING → COMPLETED |
| `test_has_edge` | True for existing, False for missing |
| `test_outgoing` | Returns all outgoing edges for a node |
| `test_all_edges` | Returns complete edge list for Kahn's algorithm |
| `test_all_nodes` | Returns complete node list for Kahn's algorithm |
| `test_get_join_time` | Returns registered timestamp, None for unknown |
| `test_edge_count` | Tracks add/remove correctly |
| `test_node_count` | Tracks register correctly |
| `test_agents_in_workflow` | Filters by workflow_id |
| `test_clear_workflow` | Removes all nodes and edges for a workflow |
| `test_snapshot_isolation` | Mutating graph after snapshot doesn't affect it |
| `test_concurrent_add_remove` | 50 threads adding/removing — no races |

#### `tests/graph/test_snapshot.py`

| Test | Description |
|---|---|
| `test_snapshot_to_json` | Serializes to valid JSON |
| `test_snapshot_to_dot` | Exports Graphviz DOT format |
| `test_snapshot_roundtrip` | JSON serialize → deserialize → equal |

#### `tests/detector/test_cycle.py`

| Test | Description |
|---|---|
| `test_cycle_2_agents` | A→B, B→A → cycle detected |
| `test_cycle_3_agents` | A→B, B→C, C→A → cycle detected |
| `test_cycle_with_tail` | A→B→C→D→B → cycle [B,C,D], A excluded |
| `test_no_cycle_linear` | A→B→C→D → no cycle |
| `test_no_cycle_diamond` | A→B, A→C, B→D, C→D → no cycle |
| `test_self_loop` | A→A → cycle of length 1 |
| `test_multiple_cycles` | Two independent cycles, both detected |
| `test_incremental_detection` | Cycle detected on the completing edge |
| `test_incremental_no_cycle` | Edge added that doesn't complete cycle → None |
| `test_depth_limit` | Cycle longer than max_cycle_length → not detected |
| `test_edge_removal_breaks_cycle` | Remove one edge → cycle gone |
| `test_full_scan_finds_cycle` | Kahn's catches existing cycle |
| `test_full_scan_acyclic` | Kahn's on DAG → empty list |
| `test_workflow_isolation` | Cycle in wf-1 ≠ cycle in wf-2 |
| `test_concurrent_edge_add` | 50 threads adding edges, detection correct |

#### `tests/detector/test_livelock.py`

| Test | Description |
|---|---|
| `test_ring_buffer_append` | Fills in order |
| `test_ring_buffer_wrap` | Wraps at capacity |
| `test_ring_buffer_last_n` | Returns correct slice |
| `test_ring_buffer_empty` | last_n on empty → empty list |
| `test_livelock_exact_match_single_direction` | Same message A→B repeated 3 times → detected via per-pair buffer |
| `test_livelock_pingpong_detected` | A→B "req" + B→A "rej" repeated 3 times → detected via conversation buffer |
| `test_livelock_below_threshold` | 2 repetitions → not detected |
| `test_livelock_pattern_length_2` | 2-message pattern repeating → detected |
| `test_livelock_pattern_length_5` | 5-message pattern repeating → detected |
| `test_livelock_no_pattern` | Random distinct messages → not detected |
| `test_livelock_progress_resets` | Progress reported → counter reset |
| `test_livelock_length_variance_suppresses` | High variance → suppressed |
| `test_livelock_multi_pair` | A-B livelock doesn't affect C-D |
| `test_livelock_window_boundary` | Pattern spanning window edge |
| `test_livelock_conversation_vs_pair` | Conversation buffer catches what pair buffer misses |
| `test_livelock_clear_workflow` | All buffers removed for cleared workflow |

#### `tests/resolver/test_resolvers.py`

| Test | Description |
|---|---|
| `test_alert_resolver_calls_callback` | on_detection callback invoked |
| `test_alert_resolver_logs` | structlog message emitted |
| `test_cancel_resolver_youngest` | Youngest agent in cycle canceled (uses `get_join_time`) |
| `test_cancel_resolver_all` | All agents canceled |
| `test_cancel_resolver_nil_fn` | cancel_fn is None → graceful skip |
| `test_tiebreaker_resolver` | tiebreaker_fn called with prompt |
| `test_escalate_resolver_success` | POST to webhook → 200 |
| `test_escalate_resolver_failure` | Webhook 500 → exception |
| `test_escalate_resolver_timeout` | Webhook timeout → exception |
| `test_chain_resolver_first_succeeds` | First resolver handles it |
| `test_chain_resolver_fallback` | First fails, second succeeds |
| `test_chain_resolver_all_fail` | All fail → raises last error |

#### `tests/integrations/test_langgraph.py`

| Test | Description |
|---|---|
| `test_tangle_node_registers_agent` | Decorated node emits Register event |
| `test_tangle_node_registers_only_once` | Second call with same workflow_id does not re-register |
| `test_tangle_node_emits_send_for_changed_keys` | Node that returns `{"draft": "..."}` emits Send event with resource="draft" |
| `test_tangle_node_emits_cancel_on_error` | Exception → Cancel event emitted, then re-raised |
| `test_tangle_node_skips_tangle_keys` | `tangle_workflow_id` key is not emitted as a Send event |
| `test_tangle_conditional_edge_emits_wait` | Conditional edge emits WaitFor to selected target |
| `test_tangle_conditional_edge_skips_end` | Edge returning `END` ("__end__") does not emit WaitFor |
| `test_full_langgraph_cycle_detection` | 3-node graph with cycle → deadlock detected |
| `test_full_langgraph_livelock_detection` | Reviewer loop → livelock detected after N iterations |

#### `tests/integrations/test_otel.py`

| Test | Description |
|---|---|
| `test_parse_wait_for_span` | Span with `tangle.event.type=wait_for` → EventType.WAIT_FOR |
| `test_parse_send_span` | Span with `tangle.event.type=send` + message hash → correct Event |
| `test_parse_register_span` | Span with `tangle.event.type=register` → EventType.REGISTER |
| `test_ignore_non_tangle_span` | Span without `tangle.*` attributes → returns None |
| `test_ignore_missing_workflow` | Span with agent_id but no workflow_id → returns None |
| `test_ignore_unknown_event_type` | `tangle.event.type=foo` → returns None |
| `test_parse_batch_mixed_spans` | Mix of Tangle and non-Tangle spans → only Tangle ones extracted |
| `test_timestamp_conversion` | `start_time_unix_nano` correctly converted to float seconds |
| `test_missing_optional_attributes` | Span without `tangle.resource` → event.resource="" |

#### `tests/server/test_routes.py`

| Test | Description |
|---|---|
| `test_post_event` | POST /v1/events → 202 |
| `test_post_event_bad_json` | Invalid body → 422 |
| `test_post_event_batch` | POST /v1/events/batch → 202 |
| `test_get_graph` | GET /v1/graph/{wf} → 200 with JSON |
| `test_get_graph_unknown` | GET /v1/graph/unknown → 200 empty |
| `test_get_detections` | GET /v1/detections → list |
| `test_get_stats` | GET /v1/stats → stats JSON |
| `test_healthz` | GET /healthz → 200 |

#### `tests/test_monitor.py` (facade)

| Test | Description |
|---|---|
| `test_deadlock_detection` | Feed deadlock_2 → detection emitted |
| `test_livelock_detection_pingpong` | Feed livelock_pingpong → detection emitted |
| `test_no_false_positive_linear` | Feed no_cycle_linear → no detection |
| `test_no_false_positive_progress` | Repetitive + progress → no detection |
| `test_edge_release_breaks_cycle` | Add cycle, release one edge → cleared |
| `test_workflow_reset` | Reset clears all state |
| `test_multiple_workflows` | Deadlock in wf-1; wf-2 unaffected |
| `test_snapshot` | Returns correct graph state |
| `test_stats` | Stats reflect state |
| `test_context_manager` | `with monitor:` start/stop lifecycle |
| `test_concurrent_process_event` | 50 threads → no races |
| `test_periodic_check_catches_missed` | Periodic scan finds cycle that incremental missed |
| `test_resolution_retry` | First resolution fails → retried next check |
| `test_otel_collector_starts_when_enabled` | OTel enabled → gRPC server starts on configured port |
| `test_otel_collector_skipped_when_disabled` | OTel disabled → no gRPC server started |

### 18.4. Integration tests

| Test file | Scenarios |
|---|---|
| `test_langgraph_e2e.py` | Compile a real LangGraph StateGraph with 3 nodes, invoke it, verify Tangle detects the designed deadlock/livelock |
| `test_otel_e2e.py` | Start Tangle with OTel collector, send OTLP spans via gRPC client, verify deadlock detected |
| `test_server_e2e.py` | Start FastAPI sidecar with `httpx.AsyncClient`, submit events, query graph and detections |
| `test_resolution_e2e.py` | Deadlock detected → cancel resolver fires → verify agent removed from WFG |

### 18.5. Property-based tests (Hypothesis)

```python
# tests/detector/test_cycle.py

from hypothesis import given, strategies as st

@given(num_agents=st.integers(min_value=2, max_value=50),
       num_edges=st.integers(min_value=1, max_value=100))
def test_cycle_detector_never_crashes(num_agents: int, num_edges: int) -> None:
    """CycleDetector handles arbitrary graphs without crashing."""
    ...

@given(data=st.data())
def test_kahns_agrees_with_incremental(data) -> None:
    """Kahn's and incremental DFS always agree on cycle existence."""
    ...
```

### 18.6. Benchmarks

```python
# tests/benchmarks/bench_cycle.py  (run with uv run pytest tests/benchmarks/ --benchmark-only)

def bench_incremental_10_agents(benchmark): ...
def bench_incremental_100_agents(benchmark): ...
def bench_incremental_1000_agents(benchmark): ...
def bench_kahns_100_agents(benchmark): ...
def bench_kahns_1000_agents(benchmark): ...
def bench_livelock_window_50(benchmark): ...
def bench_livelock_window_200(benchmark): ...
def bench_process_event(benchmark): ...
def bench_ring_buffer_append(benchmark): ...
```

---

## 19. Container-based test infrastructure

### 19.1. Dockerfile.test

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.14-alpine

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen

COPY . .
RUN uv sync --frozen
```

### 19.2. docker-compose.yml

```yaml
version: "3.9"

services:
  test-runner:
    build:
      context: .
      dockerfile: Dockerfile.test
    command: >
      sh -c "
        echo '=== Lint ===' &&
        uv run ruff check src/ tests/ &&
        echo '=== Type check ===' &&
        uv run mypy src/tangle &&
        echo '=== Unit tests ===' &&
        uv run pytest tests/ -x --ignore=tests/integration --ignore=tests/benchmarks -v --timeout=30 &&
        echo '=== Integration tests ===' &&
        uv run pytest tests/integration/ -v --timeout=60 -m integration &&
        echo '=== Coverage ===' &&
        uv run pytest tests/ --ignore=tests/benchmarks --cov=tangle --cov-report=term-missing --cov-report=html
      "
    volumes:
      - .:/app
```

### 19.3. Production Dockerfile

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.14-alpine AS builder
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --extra server --extra otel
COPY . .
RUN uv sync --frozen --no-dev --extra server --extra otel

FROM python:3.14-alpine
COPY --from=builder /app /app
WORKDIR /app
EXPOSE 4317 8090
CMD ["/app/.venv/bin/python", "-m", "tangle.cli", "--host", "0.0.0.0", "--port", "8090"]
```

### 19.4. Makefile

```makefile
.PHONY: dev test test-unit test-all lint typecheck fmt bench docker clean

dev:
	uv sync

test-unit:
	uv run pytest tests/ --ignore=tests/integration --ignore=tests/benchmarks -x -v --timeout=30

test-integration:
	uv run pytest tests/integration/ -v --timeout=60 -m integration

test-all:
	docker compose up --build --abort-on-container-exit test-runner

test: test-unit

lint:
	uv run ruff check src/ tests/

typecheck:
	uv run mypy src/tangle

fmt:
	uv run ruff format src/ tests/

bench:
	uv run pytest tests/benchmarks/ -v --benchmark-only

coverage:
	uv run pytest tests/ --ignore=tests/benchmarks --cov=tangle --cov-report=term-missing --cov-report=html

docker:
	docker build -t tangle:latest .

clean:
	docker compose down -v
	rm -rf .venv/ dist/ htmlcov/ .mypy_cache/ .pytest_cache/ *.db
```

### 19.5. CI pipeline

```yaml
# .github/workflows/ci.yml

name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  lint-and-type:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.14"
      - run: uv sync
      - run: uv run ruff check src/ tests/
      - run: uv run mypy src/tangle

  test:
    runs-on: ubuntu-latest
    needs: lint-and-type
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.14"
      - run: uv sync
      - name: Unit tests
        run: uv run pytest tests/ --ignore=tests/integration --ignore=tests/benchmarks -x --timeout=30 --cov=tangle --cov-report=xml
      - name: Integration tests
        run: uv run pytest tests/integration/ -v --timeout=60 -m integration
      - uses: actions/upload-artifact@v4
        with:
          name: coverage
          path: coverage.xml

  docker:
    runs-on: ubuntu-latest
    needs: test
    steps:
      - uses: actions/checkout@v4
      - run: make test-all
```

---

## 20. Dependencies

### 20.1. Runtime dependencies

| Package | Purpose | Version |
|---|---|---|
| `xxhash` | Fast message hashing for livelock detection | >=3.5 |
| `pydantic` | Configuration validation and serialization | >=2.10 |
| `structlog` | Structured logging | >=24.4 |

### 20.2. Optional dependencies

| Extra | Packages | Purpose |
|---|---|---|
| `langgraph` | `langgraph>=0.3`, `langchain-core>=0.3` | LangGraph decorators and hooks |
| `server` | `fastapi>=0.115`, `uvicorn>=0.34` | Standalone sidecar mode |
| `otel` | `opentelemetry-api>=1.29`, `opentelemetry-sdk>=1.29`, `opentelemetry-exporter-otlp-proto-grpc>=1.29` | OTel OTLP gRPC receiver |

### 20.3. Dev dependencies

| Package | Purpose |
|---|---|
| `pytest` | Test runner |
| `pytest-asyncio` | Async test support |
| `pytest-cov` | Coverage |
| `pytest-timeout` | Test timeouts |
| `pytest-benchmark` | Benchmark framework |
| `hypothesis` | Property-based testing |
| `ruff` | Linting + formatting |
| `mypy` | Static type checking |
| `httpx` | HTTP client for testing FastAPI |
| `grpcio` | gRPC client for testing OTel collector |
| `opentelemetry-*` | Testing OTel integration |

---

## 21. Implementation order

### Phase 1 — Foundation (week 1–2)

1. Project scaffold: `uv init`, pyproject.toml, directory structure
2. `src/tangle/types.py` — all core dataclasses and enums + `tests/test_types.py`
3. `src/tangle/config.py` — Pydantic config model + `tests/test_config.py`
4. `src/tangle/graph/wfg.py` — WaitForGraph + `tests/graph/test_wfg.py`
5. `src/tangle/graph/snapshot.py` — JSON/DOT export + `tests/graph/test_snapshot.py`
6. `tests/conftest.py` — FakeClock, MockResolver, make_event, make_detection, scenarios

**Milestone**: Can build and inspect a WFG in memory.

### Phase 2 — Detectors (week 2–3)

7. `src/tangle/detector/cycle.py` — Incremental DFS + Kahn's + `tests/detector/test_cycle.py`
8. `src/tangle/detector/livelock.py` — RingBuffer (per-pair + conversation) + pattern matcher + `tests/detector/test_livelock.py`
9. Hypothesis property-based tests for both detectors

**Milestone**: Both detectors work against in-memory graphs.

### Phase 3 — Resolution and Monitor (week 3–4)

10. `src/tangle/resolver/` — All resolver implementations + `tests/resolver/test_resolvers.py`
11. `src/tangle/monitor.py` — TangleMonitor facade + background thread + `tests/test_monitor.py`
12. `src/tangle/store/memory.py` + `sqlite.py` — stores + `tests/store/conformance.py`

**Milestone**: Full detect → resolve loop works.

### Phase 4 — Integrations (week 4–5)

13. `src/tangle/integrations/langgraph.py` — Decorators + hooks + `tests/integrations/test_langgraph.py`
14. `src/tangle/integrations/otel.py` — OTLP receiver + parser + `tests/integrations/test_otel.py`
15. Integration tests: `test_langgraph_e2e.py`, `test_otel_e2e.py`

**Milestone**: `uv add tangle-detect[langgraph]` and `uv add tangle-detect[otel]` both work.

### Phase 5 — Server and polish (week 5–6)

16. `src/tangle/server/` — FastAPI app + routes + `tests/server/test_routes.py`
17. `src/tangle/cli.py` — CLI entrypoint
18. `tests/integration/test_server_e2e.py` + `test_resolution_e2e.py`
19. `tests/benchmarks/bench_cycle.py`
20. Dockerfiles + docker-compose + Makefile + CI pipeline
21. README with quickstart, LangGraph example, and OTel example

**Milestone**: Full feature set, publishable to PyPI.

---

## 22. Open design decisions

| Decision | Default | Alternatives | Notes |
|---|---|---|---|
| Graph storage | In-memory per workflow | SQLite WAL for persistence | In-memory is sufficient since workflows are ephemeral |
| Livelock hash | xxHash of raw content | Semantic embedding bucket | xxHash is fast; semantic mode is opt-in |
| Periodic scan thread | daemon Thread | asyncio task | Thread avoids forcing async on sync LangGraph users |
| LangGraph version | >=0.3 | pin to specific minor | Loose pin allows users to upgrade LangGraph independently |
| Package name | `tangle-detect` | `tangle`, `tangle-ai` | `tangle` may be taken on PyPI; `tangle-detect` is safer |
| Free-threaded Python | Not used | PEP 734 subinterpreters | Standard threading is sufficient for the current workload |
| OTel receiver auth | Insecure (plaintext gRPC) | mTLS | Insecure is fine for sidecar on localhost; mTLS for network deployment |

---

## 23. Security considerations

- **Message content**: Only xxHash digests are stored, never raw message bodies. Full logging can be enabled via config for debugging.
- **Webhook authentication**: Escalation webhook includes `Authorization: Bearer <token>` via `TANGLE_ESCALATION_WEBHOOK_TOKEN` env var.
- **SQLite**: If using SQLite store, the file should have restricted permissions (0600). No sensitive data is stored — only detection metadata and event types.
- **LangGraph state**: Tangle reads `tangle_workflow_id` from LangGraph state. It does not read or store other state keys unless explicitly sent via `send()`.
- **OTel receiver**: The OTLP gRPC receiver listens on all interfaces by default. In production, bind to `127.0.0.1` or deploy behind a network policy. mTLS can be added via gRPC server credentials.

---

## 24. Design review checklist

- [x] **Problem clearly stated** — Section 1 defines deadlock vs livelock with production examples.
- [x] **Python 3.14 + uv** — Section 4 provides complete pyproject.toml with uv configuration, correct ruff/mypy version notes.
- [x] **LangGraph integration** — Section 10 provides fully specified decorator code with `_compute_state_keys_hash`, `_diff_keys`, and `tangle_node` / `tangle_conditional_edge`.
- [x] **OTel integration** — Section 11 provides span attribute convention, OTLP gRPC receiver, span-to-Event parser, and TypeScript usage example.
- [x] **No `from __future__ import annotations`** — Python 3.14 PEP 649 provides deferred evaluation natively; import removed to avoid Pydantic V2 issues.
- [x] **No name collisions** — Internal enum renamed to `AgentStatus` (not `AgentState`) to avoid collision with LangGraph TypedDict.
- [x] **All types defined** — Section 6 provides complete dataclass definitions with documented caveats (frozen + mutable dict).
- [x] **Core algorithms specified** — Sections 8 (deadlock: incremental DFS + Kahn's with `all_edges`/`all_nodes`) and 9 (livelock: dual-buffer conversation + per-pair pattern detection).
- [x] **Package structure complete** — Section 5 includes `tests/benchmarks/`, `test_otel_e2e.py`, and all `__init__.py` files.
- [x] **Configuration validated** — Section 7 uses `use_enum_values=True` for string↔enum coercion, `ge` constraints on numeric fields.
- [x] **Public API documented** — Section 12 (TangleMonitor), Section 10 (LangGraph), Section 11 (OTel), Section 14 (FastAPI).
- [x] **Error handling specified** — Section 17 covers all failure modes including OTel-specific errors.
- [x] **Thread safety documented** — Section 16 covers WFG locking, OTel ThreadPoolExecutor, and asyncio delegation.
- [x] **Test doubles defined** — Section 18.1 provides FakeClock, MockResolver, `make_event()`, `make_detection()`, all scenario builders.
- [x] **Conformance suite defined** — Section 18.2 provides store conformance with defined factory helpers.
- [x] **Unit tests exhaustively listed** — Section 18.3 lists 120+ test scenarios across all modules including types, config, OTel parser, and conversation-level livelock.
- [x] **Integration tests defined** — Section 18.4 includes OTel end-to-end test.
- [x] **Property-based tests** — Section 18.5 uses Hypothesis for fuzz testing detectors.
- [x] **Benchmarks defined** — Section 18.6 lists all benchmark functions; `tests/benchmarks/` in package structure.
- [x] **Container infrastructure** — Section 19 provides Dockerfile.test, docker-compose, Makefile, CI pipeline.
- [x] **Dependencies complete** — Section 20 lists `pytest-benchmark`, `grpcio`, and OTel packages in dev group.
- [x] **Implementation order** — Section 21 breaks work into 5 phases with milestones; OTel is Phase 4.
- [x] **Security considerations** — Section 23 covers OTel receiver network exposure.
- [x] **Open decisions documented** — Section 22 includes OTel auth decision.
- [x] **All tests runnable in containers** — `make test-all` runs everything via Docker.
- [x] **Implementable by Claude Code** — All types, algorithms, interfaces, test scenarios, and file paths specified.
