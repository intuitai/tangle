# src/tangle/resolver/cancel.py

from collections.abc import Callable

import structlog

from tangle.graph.wfg import WaitForGraph
from tangle.types import AgentID, Detection, ResolutionAction

logger = structlog.get_logger("tangle.resolver.cancel")


class CancelResolver:
    def __init__(
        self,
        graph: WaitForGraph,
        cancel_fn: Callable[[AgentID, str], None] | None = None,
        mode: ResolutionAction = ResolutionAction.CANCEL_YOUNGEST,
    ) -> None:
        self._graph = graph
        self._cancel_fn = cancel_fn
        self._mode = mode

    @property
    def name(self) -> str:
        return "cancel"

    @property
    def is_notification(self) -> bool:
        return False

    def resolve(self, detection: Detection) -> None:
        if not self._cancel_fn:
            logger.info("cancel_resolver_skip", reason="no cancel_fn provided")
            return

        agents = (
            detection.cycle.agents
            if detection.cycle
            else (detection.livelock.agents if detection.livelock else [])
        )

        if self._mode == ResolutionAction.CANCEL_ALL:
            for agent in agents:
                self._cancel_fn(agent, f"Canceled due to {detection.type.value}")
            logger.info("canceled_all_agents", agents=agents)
        else:
            # Cancel youngest
            youngest = self._find_youngest(agents)
            if youngest:
                self._cancel_fn(youngest, f"Canceled (youngest) due to {detection.type.value}")
                logger.info("canceled_youngest_agent", agent=youngest)

    def _find_youngest(self, agents: list[AgentID]) -> AgentID | None:
        if not agents:
            return None
        youngest: AgentID | None = None
        latest_time: float = -1.0
        for agent in agents:
            jt = self._graph.get_join_time(agent)
            if jt is not None and jt > latest_time:
                latest_time = jt
                youngest = agent
        return youngest or (agents[-1] if agents else None)
