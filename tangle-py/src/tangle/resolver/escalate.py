# src/tangle/resolver/escalate.py

import os

import structlog

from tangle.types import Detection

logger = structlog.get_logger("tangle.resolver.escalate")


class EscalateResolver:
    def __init__(self, webhook_url: str = "") -> None:
        self._webhook_url = webhook_url

    @property
    def name(self) -> str:
        return "escalate"

    def resolve(self, detection: Detection) -> None:
        if not self._webhook_url:
            logger.warning("escalate_resolver_skip", reason="no webhook_url configured")
            return

        import httpx

        payload = {
            "type": detection.type.value,
            "severity": detection.severity.value,
        }
        if detection.cycle:
            payload["cycle"] = {
                "id": detection.cycle.id,
                "agents": detection.cycle.agents,
                "workflow_id": detection.cycle.workflow_id,
            }
        if detection.livelock:
            payload["livelock"] = {
                "id": detection.livelock.id,
                "agents": detection.livelock.agents,
                "pattern_length": detection.livelock.pattern_length,
                "repeat_count": detection.livelock.repeat_count,
                "workflow_id": detection.livelock.workflow_id,
            }

        headers = {"Content-Type": "application/json"}
        token = os.environ.get("TANGLE_ESCALATION_WEBHOOK_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            resp = httpx.post(
                self._webhook_url, json=payload, headers=headers, timeout=10.0
            )
            resp.raise_for_status()
            logger.info(
                "escalation_sent", url=self._webhook_url, status=resp.status_code
            )
        except httpx.TimeoutException:
            logger.error("escalation_timeout", url=self._webhook_url)
            raise
        except httpx.HTTPStatusError as e:
            logger.error(
                "escalation_failed",
                url=self._webhook_url,
                status=e.response.status_code,
            )
            raise
