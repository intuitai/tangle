# tests/test_config.py

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tangle.config import TangleConfig
from tangle.types import ResolutionAction


class TestConfigDefaults:
    def test_defaults(self) -> None:
        cfg = TangleConfig()
        assert cfg.cycle_check_interval == 5.0
        assert cfg.max_cycle_length == 20
        assert cfg.livelock_window == 50
        assert cfg.livelock_min_repeats == 3
        assert cfg.livelock_min_pattern == 2
        assert cfg.livelock_ring_size == 200
        assert cfg.resolution == ResolutionAction.ALERT
        assert cfg.escalation_webhook_url == ""
        assert cfg.store_backend == "memory"
        assert cfg.sqlite_path == "tangle.db"
        assert cfg.otel_enabled is False
        assert cfg.otel_port == 4317
        assert cfg.server_host == "0.0.0.0"
        assert cfg.server_port == 8090


class TestResolutionField:
    def test_resolution_accepts_string(self) -> None:
        """Can pass a plain string like 'alert' for the resolution field."""
        cfg = TangleConfig(resolution="alert")
        # use_enum_values means the stored value is the string
        assert cfg.resolution == "alert"

    def test_resolution_accepts_enum(self) -> None:
        """Can pass the ResolutionAction enum directly."""
        cfg = TangleConfig(resolution=ResolutionAction.CANCEL_YOUNGEST)
        assert cfg.resolution == "cancel_youngest"

    def test_invalid_resolution_string(self) -> None:
        """Invalid resolution string raises ValidationError."""
        with pytest.raises(ValidationError):
            TangleConfig(resolution="invalid")


class TestFieldValidation:
    def test_extra_fields_forbidden(self) -> None:
        """Extra fields are forbidden (model_config extra='forbid')."""
        with pytest.raises(ValidationError):
            TangleConfig(unknown_field=1)

    def test_min_cycle_length(self) -> None:
        """max_cycle_length must be >= 2."""
        with pytest.raises(ValidationError):
            TangleConfig(max_cycle_length=1)

        # boundary: 2 should be accepted
        cfg = TangleConfig(max_cycle_length=2)
        assert cfg.max_cycle_length == 2

    def test_min_livelock_window(self) -> None:
        """livelock_window must be >= 4."""
        with pytest.raises(ValidationError):
            TangleConfig(livelock_window=2)

        with pytest.raises(ValidationError):
            TangleConfig(livelock_window=3)

        # boundary: 4 should be accepted
        cfg = TangleConfig(livelock_window=4)
        assert cfg.livelock_window == 4


class TestStoreBackend:
    def test_store_backend_validation(self) -> None:
        """store_backend only accepts 'memory' or 'sqlite'."""
        with pytest.raises(ValidationError):
            TangleConfig(store_backend="postgres")

    def test_store_backend_memory(self) -> None:
        cfg = TangleConfig(store_backend="memory")
        assert cfg.store_backend == "memory"

    def test_store_backend_sqlite(self) -> None:
        cfg = TangleConfig(store_backend="sqlite")
        assert cfg.store_backend == "sqlite"


class TestBoundaryValidation:
    def test_livelock_min_pattern_boundary(self) -> None:
        """livelock_min_pattern must be >= 1."""
        with pytest.raises(ValidationError):
            TangleConfig(livelock_min_pattern=0)
        cfg = TangleConfig(livelock_min_pattern=1)
        assert cfg.livelock_min_pattern == 1

    def test_livelock_min_repeats_boundary(self) -> None:
        """livelock_min_repeats must be >= 2."""
        with pytest.raises(ValidationError):
            TangleConfig(livelock_min_repeats=1)
        cfg = TangleConfig(livelock_min_repeats=2)
        assert cfg.livelock_min_repeats == 2

    def test_livelock_ring_size_boundary(self) -> None:
        """livelock_ring_size must be >= 10."""
        with pytest.raises(ValidationError):
            TangleConfig(livelock_ring_size=9)
        cfg = TangleConfig(livelock_ring_size=10)
        assert cfg.livelock_ring_size == 10

