# src/tangle/resolver/tiebreaker.py

from collections.abc import Callable

import structlog

from tangle.types import AgentID, Detection

logger = structlog.get_logger("tangle.resolver.tiebreaker")


class TiebreakerResolver:
    def __init__(
        self,
        tiebreaker_fn: Callable[[AgentID, str], None] | None = None,
        prompt: str = (
            "You appear to be in a loop. "
            "Please try a different approach or report that you are stuck."
        ),
    ) -> None:
        self._tiebreaker_fn = tiebreaker_fn
        self._prompt = prompt

    @property
    def name(self) -> str:
        return "tiebreaker"

    @property
    def is_notification(self) -> bool:
        return False

    def resolve(self, detection: Detection) -> None:
        if not self._tiebreaker_fn:
            logger.info("tiebreaker_resolver_skip", reason="no tiebreaker_fn provided")
            return

        agents = (
            detection.cycle.agents
            if detection.cycle
            else (detection.livelock.agents if detection.livelock else [])
        )
        if agents:
            # Send tiebreaker to the first agent in the cycle/pattern
            self._tiebreaker_fn(agents[0], self._prompt)
            logger.info("tiebreaker_sent", agent=agents[0])
