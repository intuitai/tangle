# Changelog

All notable changes to `tangle-detect` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2025-05-01

Initial release.

### Added

- **Core SDK**: `TangleMonitor` orchestrator with thread-safe event processing
  via `register()`, `wait_for()`, `send()`, `release()`, `cancel()`, and
  `complete()` hooks.
- **Deadlock detection**: Incremental DFS cycle detection on edge addition plus
  periodic full-graph scans using Kahn's algorithm (`CycleDetector`).
- **Livelock detection**: Ring-buffer pattern matching over xxhash message
  digests with configurable window size, minimum repeats, and per-pair
  isolation (`LivelockDetector`).
- **Wait-For Graph**: Thread-safe directed graph (`WaitForGraph`) with agent
  state tracking (IDLE, WAITING, ACTIVE, CANCELLED, COMPLETED).
- **Resolver chain**: Configurable resolution pipeline — `AlertResolver`,
  `CancelResolver` (youngest/all), `TiebreakerResolver` (prompt injection),
  `EscalateResolver` (webhook POST). Chain stops on first success.
- **Configuration**: `TangleConfig` Pydantic model with sensible defaults and
  strict validation (`extra=forbid`, `use_enum_values`).
- **Storage backends**: `MemoryStore` (in-memory, thread-safe) and
  `SQLiteStore` (persistent, WAL mode). Both pass a shared conformance test
  suite.
- **LangGraph integration** (Tier 1): `@tangle_node` and
  `@tangle_conditional_edge` decorators for zero-boilerplate instrumentation.
- **FastAPI sidecar** (Tier 1): REST API with `/v1/events`, `/v1/events/batch`,
  `/v1/graph/{workflow}`, `/v1/detections`, `/v1/stats`, and `/healthz`
  endpoints.
- **OpenTelemetry integration** (Tier 2): OTLP span attribute parser for
  language-agnostic monitoring.
- **CLI**: `tangle` command to launch the FastAPI sidecar via uvicorn.
- **Test suite**: 210 tests across 14 files with pytest, pytest-asyncio,
  Hypothesis property-based tests, and shared store conformance tests.

### Supported Environments

- Python 3.10, 3.11, 3.12, 3.13
- langgraph ≥0.3, langchain-core ≥0.3
- fastapi ≥0.115, uvicorn ≥0.34
- opentelemetry-api/sdk ≥1.29
- pydantic ≥2.10

[Unreleased]: https://github.com/intuitai/tangle/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/intuitai/tangle/releases/tag/v0.1.0
