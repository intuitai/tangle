# src/tangle/resolver/alert.py

from collections.abc import Callable

import structlog

from tangle.types import Detection

logger = structlog.get_logger("tangle.resolver.alert")


class AlertResolver:
    def __init__(self, on_detection: Callable[[Detection], None] | None = None) -> None:
        self._on_detection = on_detection

    @property
    def name(self) -> str:
        return "alert"

    def resolve(self, detection: Detection) -> None:
        logger.warning(
            "detection_alert",
            detection_type=detection.type.value,
            severity=detection.severity.value,
            cycle_agents=detection.cycle.agents if detection.cycle else None,
            livelock_agents=detection.livelock.agents if detection.livelock else None,
        )
        if self._on_detection:
            self._on_detection(detection)
