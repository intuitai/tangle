# src/tangle/resolver/errors.py

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tangle.types import Detection


class ResolutionExhaustedError(Exception):
    """Raised when all resolution attempts have been exhausted."""

    def __init__(
        self,
        detection: Detection,
        attempts: int,
        last_error: Exception | None = None,
    ) -> None:
        self.detection = detection
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Resolution exhausted after {attempts} attempt(s): "
            f"{detection.type.value} ({detection.severity.value})"
        )
