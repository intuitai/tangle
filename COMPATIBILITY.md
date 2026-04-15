# Compatibility Policy

This document describes the versioning, compatibility, and support commitments
for the `tangle-detect` package.

## Semantic Versioning

Tangle follows [Semantic Versioning 2.0.0](https://semver.org/):

- **MAJOR** (X.0.0) — breaking changes to the public API
- **MINOR** (0.X.0) — new features, new optional dependencies, deprecations
- **PATCH** (0.0.X) — bug fixes, performance improvements, documentation

### Pre-1.0 Policy

While Tangle is below version 1.0 (currently 0.x):

- **Minor** version bumps (e.g., 0.1 → 0.2) may contain breaking changes.
  These will always be documented in the [changelog](CHANGELOG.md) with
  migration instructions.
- **Patch** version bumps (e.g., 0.1.0 → 0.1.1) will not contain breaking
  changes.
- We aim to minimize breakage even during 0.x development and will deprecate
  before removing where practical.

### Post-1.0 Policy

Once Tangle reaches 1.0:

- Breaking changes will only occur in major version bumps.
- Deprecated APIs will carry a `DeprecationWarning` for at least one minor
  release cycle before removal.
- The public API surface is defined as: everything exported from `tangle`
  (the top-level `__init__.py`), the `TangleConfig` fields, the `Event`
  dataclass schema, and the REST API endpoint contracts in `tangle.server`.

### What Is Not Covered

Internal modules (anything not re-exported from `tangle.__init__`) may change
without notice. This includes:

- `tangle.detector.cycle` / `tangle.detector.livelock` internals
- `tangle.graph.wfg` internal methods
- `tangle.graph.snapshot` serialization format (use the REST API for stable
  graph access)
- `tangle.resolver` internal resolver classes (use `TangleConfig.resolution`
  to select behavior)

## Supported Python Versions

| Tangle Version | Python Versions       | Notes                        |
|----------------|-----------------------|------------------------------|
| 0.1.x          | 3.10, 3.11, 3.12, 3.13 | 3.10 is the minimum        |

**Policy:**

- Tangle supports the four most recent CPython minor releases.
- When a new CPython version is released, support is added in the next Tangle
  minor release.
- When a CPython version reaches end-of-life, support is dropped in the next
  Tangle minor release. This is a breaking change under post-1.0 semver rules
  and will be reflected in a major version bump after 1.0.
- PyPy and other alternative runtimes are not officially supported but may
  work. Bug reports are welcome.

## Supported Framework Versions

### Integration Dependencies

| Integration   | Required Package(s)                          | Supported Versions         |
|---------------|----------------------------------------------|----------------------------|
| **LangGraph** | `langgraph`, `langchain-core`                | langgraph ≥0.3, langchain-core ≥0.3 |
| **FastAPI**   | `fastapi`, `uvicorn`                         | fastapi ≥0.115, uvicorn ≥0.34       |
| **OpenTelemetry** | `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc` | ≥1.29 |

### Core Dependencies

| Package    | Supported Versions | Notes                                  |
|------------|--------------------|----------------------------------------|
| `pydantic` | ≥2.10              | Pydantic v1 is not supported           |
| `xxhash`   | ≥3.5.0             | Used for livelock pattern hashing      |
| `structlog`| ≥24.4              | Structured logging                     |

**Policy:**

- Tangle pins minimum versions for its dependencies (e.g., `pydantic>=2.10`).
  It does not pin maximum versions, so it should work with newer releases
  unless there is a known incompatibility.
- When an upstream dependency makes a breaking change, Tangle will release a
  patch or minor update to restore compatibility.
- Major version changes in dependencies (e.g., Pydantic v2 → v3, LangGraph
  v0 → v1) will be handled in a Tangle minor (pre-1.0) or major (post-1.0)
  release.

## Integration Tiers

Integrations are classified into two support tiers:

### Tier 1 — Fully Supported

These integrations are tested in CI on every commit, covered by the test suite,
and receive prompt bug fixes:

| Integration        | Test File                              | Test Count |
|--------------------|----------------------------------------|------------|
| **Core SDK**       | `tests/test_monitor.py`                | 29         |
| **LangGraph**      | `tests/integrations/test_langgraph.py` | 13         |
| **FastAPI Server** | `tests/server/test_routes.py`          | 14         |

### Tier 2 — Best Effort

These integrations are tested but may lag behind upstream changes. Bug reports
and contributions are welcome:

| Integration        | Test File                              | Test Count | Notes |
|--------------------|----------------------------------------|------------|-------|
| **OpenTelemetry**  | `tests/integrations/test_otel.py`      | 15         | Span attribute parsing; relies on stable OTLP proto format |

**What "best effort" means:**

- Tests exist and run in CI, but breakage from upstream OTLP/gRPC changes
  may not be caught immediately.
- Fixes are prioritized below Tier 1 issues.
- Community contributions for Tier 2 integrations are especially welcome.

### Promoting an Integration

An integration can move from Tier 2 to Tier 1 when:

1. It has comprehensive test coverage (including edge cases).
2. It is actively used in production by at least one known deployment.
3. A maintainer commits to triaging issues within one release cycle.

## Upgrade Notes

See [CHANGELOG.md](CHANGELOG.md) for detailed upgrade notes per release,
including:

- Breaking changes and migration steps
- New features and deprecations
- Bug fixes
- Dependency version changes

## Reporting Compatibility Issues

If you encounter a compatibility issue:

1. Check the [changelog](CHANGELOG.md) for known breaking changes.
2. Verify your Python version and dependency versions match the supported
   matrix above.
3. Open an issue with your environment details (`python --version`,
   `pip list | grep tangle`, and the error traceback).
