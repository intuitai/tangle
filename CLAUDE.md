# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Tangle is a Python library for detecting deadlocks and livelocks in multi-agent AI workflows. It works as an embedded library, a FastAPI sidecar, or via OpenTelemetry spans. Published to PyPI as `tangle-detect`.

## Build & Run Commands

All commands run from the repo root:

```bash
# Install (core + dev + all optional extras)
uv sync --all-extras

# Run full test suite (210 tests)
uv run pytest

# Run a single test file / class / method
uv run pytest tests/test_monitor.py
uv run pytest tests/test_monitor.py::TestDeadlockDetection
uv run pytest tests/detector/test_cycle.py::TestFullScan::test_full_scan_finds_cycle

# Skip slow/integration tests
uv run pytest -m "not slow and not integration"

# Coverage
uv run pytest --cov=tangle --cov-report=term-missing

# Lint & format
uv run ruff check src/ tests/          # lint
uv run ruff check --fix src/ tests/    # lint + autofix
uv run ruff format src/ tests/         # format
uv run mypy src/                       # type check
```

## Architecture

### Event Flow

Events enter via SDK hooks (`monitor.wait_for()`, `.send()`, etc.), the FastAPI REST API (`/v1/events`), or LangGraph decorators. All paths converge on `TangleMonitor.process_event()`, which:

1. Records the event in the Store
2. Updates the WaitForGraph (adds/removes edges, changes agent states)
3. Runs the appropriate detector:
   - **WAIT_FOR** events trigger `CycleDetector.on_edge_added()` (incremental DFS)
   - **SEND** events trigger `LivelockDetector.on_message()` (ring-buffer pattern matching)
   - **PROGRESS** events reset livelock buffers
4. If a detection occurs, runs the `ResolverChain`

A background thread periodically runs `CycleDetector.full_scan()` (Kahn's algorithm) as a safety net for cycles that incremental detection might miss.

### Key Design Decisions

- **Thread safety**: `TangleMonitor` uses `threading.RLock`; `WaitForGraph` uses `threading.RLock`; `RingBuffer` uses `threading.Lock`. All public methods are thread-safe.
- **ResolverChain semantics**: Tries resolvers in order, stops on first success. `AlertResolver` is always first. If it raises (e.g., callback fails), the chain falls through to the next resolver (cancel/tiebreaker/escalate). This means action resolvers only fire if AlertResolver fails.
- **FakeClock pattern**: Tests inject a deterministic clock via `TangleMonitor(clock=fake_clock)` to avoid time-dependent flakiness. The `FakeClock` class is in `tests/conftest.py`.
- **Store conformance**: Both `MemoryStore` and `SQLiteStore` must pass the shared test suite in `tests/store/conformance.py`. New store backends should be validated the same way.

### Module Responsibilities

| Module | Role |
|---|---|
| `monitor.py` | Orchestrator — wires graph, detectors, resolvers, store together |
| `types.py` | All domain types — `Event` (frozen), `Edge`, `Cycle`, `LivelockPattern`, `Detection`, enums |
| `config.py` | `TangleConfig` Pydantic model (extra=forbid, use_enum_values) |
| `detector/cycle.py` | Incremental DFS (`on_edge_added`) + Kahn's full scan (`full_scan`) |
| `detector/livelock.py` | `RingBuffer` + xxhash digests, per-pair and per-workflow conversation buffers |
| `graph/wfg.py` | `WaitForGraph` — thread-safe directed graph with agent state tracking |
| `resolver/chain.py` | `ResolverChain` — fallthrough-on-failure semantics |
| `server/routes.py` | FastAPI endpoints; `_to_event()` converts HTTP requests to `Event` objects |
| `integrations/langgraph.py` | `@tangle_node` and `@tangle_conditional_edge` decorators |
| `integrations/otel.py` | Parses OTLP span attributes (`tangle.*`) into `Event` objects |

## Conventions

- **Python 3.10** required (`requires-python = ">=3.10"`)
- **Ruff** for linting (line-length=100, rules: E/F/I/UP/B/SIM/TCH)
- **Ruff** for formatting (via `ruff format`)
- **Mypy** strict mode
- **pytest-asyncio** with `asyncio_mode = "auto"` — async tests don't need the `@pytest.mark.asyncio` decorator
- **Hypothesis** property-based tests exist in `tests/detector/test_cycle.py`
- Test markers: `@pytest.mark.integration`, `@pytest.mark.slow`
- `Event` is a frozen dataclass — never mutate events after creation
- `Edge`, `Cycle`, `LivelockPattern` are mutable dataclasses with `slots=True`
