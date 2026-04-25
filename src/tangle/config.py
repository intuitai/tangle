# src/tangle/config.py

from pydantic import BaseModel, Field

from tangle.types import ResolutionAction, ResolutionFailurePolicy


class TangleConfig(BaseModel):
    model_config = {"extra": "forbid", "use_enum_values": True}

    cycle_check_interval: float = Field(
        default=5.0, description="Seconds between periodic full-graph scans"
    )
    max_cycle_length: int = Field(
        default=20, ge=2, description="Maximum cycle length to search for"
    )

    livelock_window: int = Field(
        default=50, ge=4, description="Number of recent messages to analyze"
    )
    livelock_min_repeats: int = Field(
        default=3, ge=2, description="Minimum pattern repetitions to trigger"
    )
    livelock_min_pattern: int = Field(
        default=2, ge=1, description="Minimum messages per pattern iteration"
    )
    livelock_ring_size: int = Field(
        default=200, ge=10, description="Ring buffer capacity per agent pair"
    )
    resolution: ResolutionAction = Field(default=ResolutionAction.ALERT)
    resolution_failure_policy: ResolutionFailurePolicy = Field(
        default=ResolutionFailurePolicy.IGNORE,
        description=(
            "What to do when all resolvers fail: "
            "ignore (log only), raise (propagate to caller), "
            "mark_unresolved (flag the detection), "
            "retry_webhook (retry escalation with backoff), "
            "retry_chain (retry entire chain with backoff)"
        ),
    )
    max_resolution_attempts: int = Field(
        default=3,
        ge=1,
        description="Maximum retry attempts for retry_webhook/retry_chain failure policies",
    )
    resolution_retry_base_delay: float = Field(
        default=1.0,
        gt=0,
        description="Base delay in seconds for exponential backoff on resolution retries",
    )
    escalation_webhook_url: str = Field(default="")
    tiebreaker_prompt: str = Field(
        default=(
            "You appear to be in a loop. "
            "Please try a different approach or report that you are stuck."
        )
    )

    store_backend: str = Field(default="memory", pattern="^(memory|sqlite)$")
    sqlite_path: str = Field(default="tangle.db")

    event_log_path: str = Field(
        default="",
        description=(
            "Path to an append-only JSONL event log. Empty disables logging. "
            "When set, every processed event is written for deterministic replay."
        ),
    )
    event_log_fsync: bool = Field(
        default=True,
        description=(
            "Call fsync after each event log append. Safer but slower; disable "
            "on hot paths where the log is replicated elsewhere."
        ),
    )

    otel_enabled: bool = Field(
        default=False, description="Enable OTLP gRPC span receiver and log export"
    )
    otel_port: int = Field(default=4317, description="OTLP gRPC receiver port")
    otel_log_endpoint: str = Field(
        default="http://localhost:4317",
        description="OTLP gRPC endpoint for log export",
    )
    service_name: str = Field(
        default="tangle",
        description="OTel service.name resource attribute",
    )

    metrics_enabled: bool = Field(
        default=False,
        description=(
            "Enable Prometheus metrics collection. When true, the FastAPI sidecar "
            "exposes them at GET /v1/metrics; embedded callers can read "
            "TangleMonitor.metrics directly."
        ),
    )

    server_host: str = Field(default="0.0.0.0")
    server_port: int = Field(default=8090)

    api_auth_token: str = Field(
        default="",
        description=(
            "Bearer token required on all /v1 routes. Empty disables auth "
            "(intended only for local/dev sidecars behind a trusted network)."
        ),
    )
    api_idempotency_cache_size: int = Field(
        default=1024,
        ge=0,
        description=(
            "Number of recent Idempotency-Key values to remember for event ingestion. "
            "0 disables idempotency caching; duplicate keys then re-process normally."
        ),
    )

    retention_completed_workflow_ttl: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Seconds to keep a workflow whose agents are all COMPLETED/CANCELED. "
            "0 disables age-based eviction (keeps every workflow until reset_workflow). "
            "Eviction clears graph state, livelock buffers, and resolved detections."
        ),
    )
    max_active_workflows: int = Field(
        default=0,
        ge=0,
        description=(
            "Soft cap on concurrently tracked workflows. When exceeded, terminal "
            "workflows are evicted first (ignoring TTL). If no terminal workflow "
            "exists to evict, the overflow is recorded in metrics and logged but "
            "no in-flight workflow is forcibly removed. 0 disables the cap."
        ),
    )
    max_events_in_memory: int = Field(
        default=0,
        ge=0,
        description=(
            "Maximum events retained by MemoryStore. When exceeded, oldest events "
            "are dropped (ring-buffer semantics) and counted as evictions. "
            "0 disables the cap. Has no effect on SQLiteStore."
        ),
    )
    retention_check_interval: float = Field(
        default=30.0,
        gt=0,
        description=(
            "Seconds between retention sweeps. Sweeps run on the background scan "
            "thread alongside cycle detection; each sweep evicts expired workflows "
            "and updates retention metrics."
        ),
    )
