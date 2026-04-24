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

    metrics_enabled: bool = Field(default=False, description="Enable Prometheus metrics collection")
    metrics_port: int = Field(
        default=9090, description="Port for standalone Prometheus metrics HTTP server"
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
