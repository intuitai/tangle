# Tangle

[![Tests](https://github.com/intuitai/tangle/actions/workflows/tests.yml/badge.svg)](https://github.com/intuitai/tangle/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Coverage](https://img.shields.io/badge/coverage-98%25-brightgreen.svg)](#running-tests)

Deadlock and livelock detection for multi-agent AI workflows.

Tangle monitors agent interactions in real time, detects when agents are stuck
(deadlocks) or looping without progress (livelocks), and triggers configurable
resolution actions. It works as an embedded Python library or as a standalone
FastAPI sidecar, with native support for LangGraph and language-agnostic
OpenTelemetry (OTLP) integration.

## Architecture

```
                         Event Sources
          ┌────────────────┬──────────────────┐
          │                │                  │
   SDK Hooks        LangGraph            FastAPI
  .wait_for()    Decorators            Sidecar
  .send()        @tangle_node         POST /v1/events
  .release()     @tangle_conditional  POST /v1/events/batch
  .cancel()        _edge
  .complete()          │                  │
          │            │                  │
          └────────────┼──────────────────┘
                       │
                       v
              ┌─────────────────┐
              │  TangleMonitor  │     Main orchestrator
              │ process_event() │     (thread-safe)
              └────────┬────────┘
                       │
          ┌────────────┼────────────┐
          v            v            v
   ┌────────────┐ ┌──────────┐ ┌────────┐
   │ WaitFor-   │ │ Cycle    │ │Livelock│
   │ Graph      │ │ Detector │ │Detector│
   │            │ │          │ │        │
   │ Tracks     │ │Incremental│ │Ring    │
   │ agent      │ │DFS on    │ │buffer  │
   │ edges &    │ │edge add  │ │pattern │
   │ states     │ │          │ │match   │
   │            │ │Periodic  │ │(xxhash)│
   │            │ │Kahn's    │ │        │
   │            │ │full scan │ │        │
   └────────────┘ └─────┬────┘ └───┬────┘
                        │          │
                        v          v
                   ┌─────────────────┐
                   │   Detection     │
                   │  (Deadlock or   │
                   │   Livelock)     │
                   └────────┬────────┘
                            │
                            v
                   ┌─────────────────┐
                   │ Resolver Chain  │
                   │                 │
                   │ 1. Alert        │──> callback / log
                   │ 2. Cancel       │──> cancel youngest or all agents
                   │ 3. Tiebreaker   │──> inject prompt to break loop
                   │ 4. Escalate     │──> POST to webhook
                   │                 │
                   │ (stops on first │
                   │  success)       │
                   └────────┬────────┘
                            │
                            v
                   ┌─────────────────┐
                   │     Store       │
                   │                 │
                   │ MemoryStore     │
                   │ SQLiteStore     │
                   └─────────────────┘
```

Events flow into `TangleMonitor` from three possible sources: direct SDK hook
calls, LangGraph decorators that automatically emit events, or the FastAPI
sidecar REST API. Every event passes through `process_event()`, which updates
the Wait-For Graph and runs the appropriate detector. `WAIT_FOR` events trigger
cycle detection (deadlocks). `SEND` events trigger pattern matching (livelocks).
When a detection occurs, the resolver chain executes in order and stops on the
first resolver that succeeds. All events and detections are persisted to the
configured store backend.

## Detecting deadlocks in LangGraph

Multi-agent LangGraph workflows can deadlock when agents form circular
dependencies -- Agent A waits for Agent B, which waits for Agent C, which
waits for Agent A. These deadlocks cause the workflow to hang silently with
no error. Tangle solves this by maintaining a Wait-For Graph of agent
dependencies and detecting cycles in real time.

Agents can also livelock -- repeatedly exchanging the same messages (e.g.,
request/reject loops) without making progress. Tangle detects this by hashing
message content and matching repeated patterns in a sliding window.

Instrument a LangGraph workflow with two decorators:

```python
from langgraph.graph import StateGraph
from tangle import TangleConfig, TangleMonitor
from tangle.integrations.langgraph import tangle_node, tangle_conditional_edge

# Configure detection and resolution
config = TangleConfig(
    resolution="cancel_youngest",     # cancel the most recently joined agent
    livelock_min_repeats=3,           # flag after 3 repeated message patterns
)

def handle_cancel(agent_id, reason):
    print(f"Canceling {agent_id}: {reason}")

monitor = TangleMonitor(
    config=config,
    on_detection=lambda d: print(f"DETECTED: {d.type.value}"),
    cancel_fn=handle_cancel,
)

# Wrap each node -- auto-emits REGISTER, SEND, and CANCEL events
@tangle_node(monitor, agent_id="researcher")
def researcher(state):
    return {"findings": do_research(state["topic"])}

@tangle_node(monitor, agent_id="writer")
def writer(state):
    return {"draft": write_draft(state["findings"])}

@tangle_node(monitor, agent_id="reviewer")
def reviewer(state):
    return {"feedback": review(state["draft"])}

# Wrap conditional edges -- auto-emits WAIT_FOR events
@tangle_conditional_edge(monitor, from_agent="reviewer")
def route_after_review(state):
    if state["feedback"] == "approved":
        return "__end__"
    return "writer"           # sends writer back to revise -> potential loop

# Build the graph as usual
graph = StateGraph(dict)
graph.add_node("researcher", researcher)
graph.add_node("writer", writer)
graph.add_node("reviewer", reviewer)
graph.set_entry_point("researcher")
graph.add_edge("researcher", "writer")
graph.add_edge("writer", "reviewer")
graph.add_conditional_edges("reviewer", route_after_review)
app = graph.compile()

# Run with tangle_workflow_id in state to enable tracking
with monitor:
    result = app.invoke({
        "topic": "AI safety",
        "tangle_workflow_id": "wf-1",
    })
```

The decorators emit events transparently. If `writer` and `reviewer` enter a
reject/revise loop, Tangle detects the repeated message pattern and cancels the
youngest agent. If a more complex multi-agent graph forms a circular dependency,
the cycle detector catches it within milliseconds.

## Features

- **Deadlock detection** -- incremental cycle detection on a Wait-For Graph
  with sub-second latency, plus periodic full-graph scans via Kahn's algorithm.
- **Livelock detection** -- ring-buffer pattern matching over message digests
  (xxhash) to catch repetitive request/reject loops.
- **Configurable resolution** -- alert, cancel youngest, cancel all, tiebreaker
  prompt injection, or webhook escalation. Resolvers are chained and fall
  through on failure.
- **LangGraph integration** -- `@tangle_node` and `@tangle_conditional_edge`
  decorators for zero-boilerplate instrumentation.
- **OpenTelemetry integration** -- parse Tangle events from OTLP spans for
  language-agnostic monitoring.
- **FastAPI sidecar** -- REST API for submitting events, querying the graph,
  and inspecting detections.
- **Persistent storage** -- in-memory or SQLite backends for event and
  detection history.

## Requirements

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Installation

```bash
# Install core library
uv pip install .

# Install with optional extras
uv pip install ".[langgraph]"     # LangGraph decorators
uv pip install ".[server]"        # FastAPI sidecar
uv pip install ".[otel]"          # OpenTelemetry span parsing
```

## Quick start

### Embedded library

```python
from tangle import TangleConfig, TangleMonitor

config = TangleConfig(resolution="alert")
monitor = TangleMonitor(config=config, on_detection=print)

monitor.register("wf-1", "AgentA")
monitor.register("wf-1", "AgentB")
monitor.wait_for("wf-1", "AgentA", "AgentB")
monitor.wait_for("wf-1", "AgentB", "AgentA")  # deadlock detected
```

### LangGraph decorators

```python
from tangle import TangleMonitor
from tangle.integrations.langgraph import tangle_node, tangle_conditional_edge

monitor = TangleMonitor()

@tangle_node(monitor, agent_id="writer")
def writer_node(state):
    return {"draft": "Hello, world!"}

@tangle_conditional_edge(monitor, from_agent="router")
def route(state):
    return "writer"
```

### FastAPI sidecar

```bash
# Start the sidecar server
tangle --host 0.0.0.0 --port 8090
```

Endpoints (all under `/v1`):

| Method | Path                    | Description              |
|--------|-------------------------|--------------------------|
| POST   | `/v1/events`            | Submit a single event    |
| POST   | `/v1/events/batch`      | Submit events in batch   |
| GET    | `/v1/graph/{workflow}`  | Get workflow graph state |
| GET    | `/v1/detections`        | List active detections   |
| GET    | `/v1/stats`             | Monitor statistics       |
| GET    | `/healthz`              | Health check             |

## Examples

### LangGraph deadlock detection

The `tangle-py/examples/langgraph_deadlock_detection.py` script shows a four-agent
research pipeline — researcher, writer, reviewer, and editor — where the review/edit
cycle creates a circular wait dependency:

```
researcher -> writer -> reviewer -> editor -> researcher
```

When each agent waits on the next and the last waits back on the first, no agent can
make progress. Tangle detects the cycle the moment the closing edge is added.

The example has two parts:

1. A normal LangGraph workflow run (no deadlock) showing that `@tangle_node` and
   `@tangle_conditional_edge` instrument the graph transparently.
2. A simulated deadlock using `monitor.wait_for()` SDK calls to inject a four-agent
   cycle directly, triggering the on-detection callback.

**Run it:**

```bash
cd tangle-py && uv run python examples/langgraph_deadlock_detection.py
```

**Key instrumentation:**

```python
config = TangleConfig(resolution="cancel_youngest")
monitor = TangleMonitor(config=config, on_detection=on_detection, cancel_fn=cancel_agent)

@tangle_node(monitor, agent_id="researcher")
def researcher_node(state): ...

@tangle_conditional_edge(monitor, from_agent="editor")
def editor_route(state):
    return "researcher"   # back-edge that could close a cycle

with monitor:
    app.invoke({"tangle_workflow_id": "wf-1", ...})
```

**Expected output (abridged):**

```
[1] Running normal LangGraph workflow...
[1] Workflow completed after 3 review iteration(s).

[2] Now simulating a deadlock scenario...
[deadlock-sim] editor is waiting for researcher  (cycle closes here!)

[TANGLE] DEADLOCK DETECTED!
  Cycle:    researcher -> writer -> reviewer -> editor -> researcher
  Agents in deadlock: 4

[2] Active detections: 1
    Type: DEADLOCK | Cycle length: 4 agents | Resolved: False
```

The `cancel_youngest` resolver cancels the most recently registered agent to break
the cycle. Swap it for `cancel_all`, `tiebreaker`, or `escalate` in `TangleConfig`
to change the resolution behavior.

### Customer support escalation (livelock + deadlock)

The `tangle-py/examples/customer_support_escalation.py` script demonstrates both
failure modes in a four-agent support pipeline — triage, researcher, drafter, and
reviewer:

```
triage -> researcher -> drafter <-> reviewer (reject/revise loop)
            ^                          |
            └──────────────────────────┘  (circular wait)
```

1. **Livelock:** The drafter and reviewer exchange the same reject/revise messages
   in a loop. Tangle detects the repeated pattern and injects a tiebreaker prompt.
2. **Deadlock:** All four agents form a circular wait. Tangle detects the cycle
   instantly via incremental DFS.

No external dependencies — uses the SDK directly (no LangGraph required):

```bash
cd tangle-py && uv run python examples/customer_support_escalation.py
```

## Configuration

`TangleConfig` is a Pydantic model. All fields have sensible defaults:

| Field                     | Default   | Description                          |
|---------------------------|-----------|--------------------------------------|
| `cycle_check_interval`    | `5.0`     | Seconds between periodic full scans  |
| `max_cycle_length`        | `20`      | Maximum cycle depth for DFS          |
| `livelock_window`         | `50`      | Messages to analyze for patterns     |
| `livelock_min_repeats`    | `3`       | Minimum pattern repetitions          |
| `livelock_min_pattern`    | `2`       | Minimum messages per pattern         |
| `livelock_ring_size`      | `200`     | Ring buffer capacity per agent pair  |
| `resolution`              | `"alert"` | Resolution action (see below)        |
| `store_backend`           | `"memory"`| `"memory"` or `"sqlite"`             |

Resolution actions: `alert`, `cancel_youngest`, `cancel_all`, `tiebreaker`,
`escalate`.

## Development

### Setup

```bash
# Clone and install all dependencies (core + dev + all extras)
uv sync --all-extras
```

### Running tests

```bash
# Run the full test suite
uv run pytest

# Verbose output with full tracebacks
uv run pytest -v --tb=long

# Run a specific test file
uv run pytest tests/test_monitor.py

# Run a specific test class or method
uv run pytest tests/test_monitor.py::TestDeadlockDetection
uv run pytest tests/detector/test_cycle.py::TestFullScan::test_full_scan_finds_cycle

# Run tests with coverage
uv run pytest --cov=tangle --cov-report=term-missing

# Run only fast tests (exclude slow/integration markers)
uv run pytest -m "not slow and not integration"
```

### Test suite overview

The test suite contains **210 tests** across 14 test files:

| Area               | File(s)                             | Tests | What is covered                                       |
|--------------------|-------------------------------------|-------|-------------------------------------------------------|
| Monitor            | `test_monitor.py`                   | 29    | Deadlock/livelock detection, SDK hooks, resolver wiring, periodic scan, concurrency |
| Types              | `test_types.py`                     | 14    | Event immutability, enum values, Cycle/Edge/LivelockPattern/Detection fields |
| Config             | `test_config.py`                    | 14    | Defaults, resolution field, boundary validation, store backend |
| CLI                | `test_cli.py`                       | 4     | Argument parsing, server startup, graceful shutdown    |
| Cycle detector     | `detector/test_cycle.py`            | 19    | Incremental DFS, Kahn's full scan, depth limits, concurrency, Hypothesis property tests |
| Livelock detector  | `detector/test_livelock.py`         | 21    | RingBuffer operations, pattern matching, progress reset, multi-pair isolation |
| Wait-For Graph     | `graph/test_wfg.py`                 | 22    | Edge add/remove, agent registration, state transitions, workflow clear, concurrency |
| Graph snapshot     | `graph/test_snapshot.py`            | 9     | JSON/DOT serialization, round-trip, error handling     |
| Resolvers          | `resolver/test_resolvers.py`        | 34    | Alert, cancel, tiebreaker, escalate resolvers; chain fallback; bearer token; edge cases |
| Server routes      | `server/test_routes.py`             | 14    | All REST endpoints, error paths, batch detection, livelock serialization |
| Memory store       | `store/test_memory.py`              | 1     | Conformance suite (shared via `store/conformance.py`)  |
| SQLite store       | `store/test_sqlite.py`              | 1     | Conformance suite (shared via `store/conformance.py`)  |
| LangGraph          | `integrations/test_langgraph.py`    | 13    | Node/edge decorators, default workflow, non-dict returns, args forwarding |
| OpenTelemetry      | `integrations/test_otel.py`         | 15    | Span parsing for all 7 event types, int_value extraction, invalid hex, missing attributes |

### Linting and formatting

```bash
# Format with ruff
uv run ruff format src/ tests/

# Lint with ruff
uv run ruff check src/ tests/

# Lint and auto-fix
uv run ruff check --fix src/ tests/

# Type check with mypy
uv run mypy src/
```

## Project structure

```
tangle-py/
  src/tangle/
    __init__.py          # Public API exports
    types.py             # Core types: Event, Edge, Cycle, Detection, enums
    config.py            # TangleConfig (Pydantic model)
    monitor.py           # TangleMonitor (main orchestrator)
    cli.py               # CLI entry point (uvicorn runner)
    detector/
      cycle.py           # CycleDetector (incremental DFS + Kahn's)
      livelock.py        # LivelockDetector (ring buffer + pattern matching)
    graph/
      wfg.py             # WaitForGraph (thread-safe directed graph)
      snapshot.py         # GraphSnapshot (serializable graph state)
    resolver/
      chain.py           # ResolverChain (tries in order, stops on success)
      alert.py           # AlertResolver (callback + logging)
      cancel.py          # CancelResolver (cancel youngest or all)
      tiebreaker.py      # TiebreakerResolver (inject prompt)
      escalate.py        # EscalateResolver (POST to webhook)
    store/
      memory.py          # MemoryStore (in-memory, thread-safe)
      sqlite.py          # SQLiteStore (persistent, WAL mode)
    server/
      app.py             # FastAPI app factory
      routes.py          # REST endpoints
    integrations/
      langgraph.py       # @tangle_node, @tangle_conditional_edge
      otel.py            # OpenTelemetry span parser
  tests/                 # Mirror of src/ structure
  pyproject.toml         # Build config, dependencies, tool settings
```

## License

Apache 2.0
