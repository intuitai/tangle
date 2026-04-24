# Tangle HTTP API

This document is the client contract for the Tangle sidecar. The FastAPI
application also serves a live OpenAPI schema at `/openapi.json` and
interactive docs at `/docs` (Swagger UI) and `/redoc`.

## Versioning

All product endpoints live under a major-version prefix (currently `/v1`).

- **Additive changes** (new endpoints, new optional query parameters, new
  fields in responses) are backward-compatible and can be released without
  bumping the version prefix. Clients should ignore unknown fields.
- **Breaking changes** (removing or renaming fields, changing types,
  removing endpoints, tightening validation) require a new prefix (`/v2`,
  ...). Both versions are served in parallel for at least one minor
  release before the older surface is removed.
- `/healthz` is *not* versioned. It is a liveness probe and must remain
  stable across versions.

## Authentication

The sidecar supports a single static bearer token:

```
Authorization: Bearer <api_auth_token>
```

The token is configured via `TangleConfig.api_auth_token` (or the
`TANGLE_API_AUTH_TOKEN` env var, if you wire one in). When the token is
empty, auth is disabled — this is intended only for local development or
for sidecars running inside a trusted network boundary.

- Token comparison uses `hmac.compare_digest` to avoid timing leaks.
- Failures return `401 Unauthorized` with `WWW-Authenticate: Bearer`.
- `/healthz` is always public so Kubernetes-style probes keep working.

Future versions may add OIDC / mTLS; the header contract will not change.

## Idempotency

`POST /v1/events` and `POST /v1/events/batch` accept an `Idempotency-Key`
header. Clients should generate a unique key per logical request (UUID
v4 is fine) and reuse it when retrying on network errors or 5xx.

Semantics:

- If the same key is seen within the cache window (default 1024 most
  recent keys, LRU), the server returns the original response with
  `idempotent_replay: true` and does **not** reprocess the events.
- Cache entries are bound to a sha256 of the request body, so a client
  that accidentally reuses a key with a different payload will *not* get
  a stale reply. Both requests are processed.
- The cache is process-local. In a multi-replica deployment, route
  retries back to the same replica or layer a shared dedup store on top.
- Without an `Idempotency-Key` header, ingestion is at-least-once:
  retries will produce duplicate events. Tangle's detectors tolerate
  duplicate `wait_for`/`release` pairs, but duplicate `send` events can
  inflate livelock buffers. Prefer idempotency keys in production.

Set `TangleConfig.api_idempotency_cache_size=0` to disable the cache.

## Endpoints

| Method | Path                    | Description                      |
|--------|-------------------------|----------------------------------|
| POST   | `/v1/events`            | Submit a single event            |
| POST   | `/v1/events/batch`      | Submit a batch of events         |
| GET    | `/v1/graph/{workflow}`  | Get the wait-for graph           |
| GET    | `/v1/detections`        | List detections (paginated)      |
| GET    | `/v1/stats`             | Monitor statistics               |
| GET    | `/v1/metrics`           | Prometheus metrics (if enabled)  |
| GET    | `/healthz`              | Liveness probe (unversioned)     |

### POST /v1/events

```json
{
  "type": "wait_for",
  "workflow_id": "wf-42",
  "from_agent": "writer",
  "to_agent": "reviewer",
  "resource": "draft-v2",
  "message_body": "",
  "timestamp": null
}
```

`type` must be one of `wait_for`, `release`, `send`, `register`,
`complete`, `cancel`, `progress`. `message_body` is a hex string; non-hex
values are accepted and UTF-8 encoded for backward compatibility.
`timestamp` is optional — the server assigns one from the monitor clock
if omitted.

Response `202 Accepted`:

```json
{
  "accepted": true,
  "detection": false,
  "idempotent_replay": false
}
```

`detection` is `true` when processing this event triggered a deadlock or
livelock detection. Use `GET /v1/detections` to inspect details.

### POST /v1/events/batch

Same event payload wrapped in `{"events": [...]}`. Returns the count of
events processed and how many triggered a detection.

### GET /v1/detections

Query parameters (all optional):

| Name          | Type    | Default | Notes                                                |
|---------------|---------|---------|------------------------------------------------------|
| `workflow_id` | string  | --      | Exact match                                          |
| `type`        | enum    | --      | `deadlock` or `livelock`                             |
| `severity`    | enum    | --      | `warning` or `critical`                              |
| `resolved`    | bool    | --      | Omitted: only unresolved. `true`/`false` filter both |
| `limit`       | int     | 100     | 1--1000                                              |
| `offset`      | int     | 0       | >= 0                                                 |

Response:

```json
{
  "items": [
    {
      "type": "deadlock",
      "severity": "critical",
      "resolved": false,
      "workflow_id": "wf-42",
      "cycle": {"agents": ["A", "B"], "workflow_id": "wf-42"},
      "livelock": null
    }
  ],
  "total": 1,
  "limit": 100,
  "offset": 0
}
```

`total` is the count *after* filters are applied, not the lifetime
detection count.

### GET /v1/graph/{workflow_id}

Unknown workflows return an empty snapshot with status 200, so clients
can poll without special-casing "not found".

### GET /v1/stats, /v1/metrics, /healthz

See `/docs` for the live schema. `/v1/metrics` returns 404 when
`TangleConfig.metrics_enabled` is false.

## Error model

Validation errors (schema mismatch, unknown enum value, out-of-range
query param) return `422` with FastAPI's default error envelope. Auth
failures return `401`. Server errors return `500` with `{"detail":
"..."}` -- the detail message is safe to surface to operators but should
not be parsed by clients.
