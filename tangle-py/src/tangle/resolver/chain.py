# src/tangle/resolver/chain.py

import structlog

from tangle.resolver.base import Resolver
from tangle.types import Detection

logger = structlog.get_logger("tangle.resolver.chain")


class ResolverChain:
    """Tries resolvers in order; stops on first success."""

    def __init__(self, resolvers: list[Resolver] | None = None) -> None:
        self._resolvers: list[Resolver] = resolvers or []

    def add(self, resolver: Resolver) -> None:
        self._resolvers.append(resolver)

    @property
    def name(self) -> str:
        return "chain"

    def resolve(self, detection: Detection) -> None:
        # Phase 1: run all notification resolvers (always, don't stop on success/failure)
        last_notification_error: Exception | None = None
        for resolver in self._resolvers:
            if resolver.is_notification:
                try:
                    resolver.resolve(detection)
                    logger.info("resolver_succeeded", resolver=resolver.name)
                except Exception as e:
                    logger.warning("resolver_failed", resolver=resolver.name, error=str(e))
                    last_notification_error = e

        # Phase 2: run remediation resolvers (stop on first success)
        last_error: Exception | None = last_notification_error
        for resolver in self._resolvers:
            if not resolver.is_notification:
                try:
                    resolver.resolve(detection)
                    logger.info("resolver_succeeded", resolver=resolver.name)
                    return
                except Exception as e:
                    logger.warning("resolver_failed", resolver=resolver.name, error=str(e))
                    last_error = e
        if last_error:
            raise last_error
