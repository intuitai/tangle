# src/tangle/config.py

from pydantic import BaseModel, Field

from tangle.types import ResolutionAction


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
    livelock_semantic: bool = Field(
        default=False, description="Enable semantic hashing"
    )

    resolution: ResolutionAction = Field(default=ResolutionAction.ALERT)
    escalation_webhook_url: str = Field(default="")
    tiebreaker_prompt: str = Field(
        default=(
            "You appear to be in a loop. "
            "Please try a different approach or report that you are stuck."
        )
    )

    event_queue_size: int = Field(default=10_000, ge=100)

    store_backend: str = Field(default="memory", pattern="^(memory|sqlite)$")
    sqlite_path: str = Field(default="tangle.db")

    otel_enabled: bool = Field(
        default=False, description="Enable OTLP gRPC span receiver"
    )
    otel_port: int = Field(default=4317, description="OTLP gRPC receiver port")

    server_host: str = Field(default="0.0.0.0")
    server_port: int = Field(default=8090)
