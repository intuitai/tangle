# src/tangle/resolver/chain.py

import time
from collections.abc import Callable

import structlog

from tangle.resolver.base import Resolver
from tangle.resolver.errors import ResolutionExhaustedError
from tangle.types import Detection, ResolutionFailurePolicy

logger = structlog.get_logger("tangle.resolver.chain")


class ResolverChain:
    """Tries resolvers in order; stops on first success.

    When all remediation resolvers fail, the configured failure policy
    determines what happens next:

    - ``ignore``:  log a warning and return (default, preserves old behaviour)
    - ``raise``:   raise ``ResolutionExhaustedError`` to the caller
    - ``mark_unresolved``: set ``detection.resolution_exhausted = True``
    - ``retry_webhook``:   retry only escalation resolvers with exponential backoff
    - ``retry_chain``:     retry the entire remediation phase with exponential backoff
    """

    def __init__(
        self,
        resolvers: list[Resolver] | None = None,
        failure_policy: ResolutionFailurePolicy = ResolutionFailurePolicy.IGNORE,
        max_attempts: int = 3,
        retry_base_delay: float = 1.0,
        clock: Callable[[float], object] = time.sleep,
    ) -> None:
        self._resolvers: list[Resolver] = resolvers or []
        self._failure_policy = failure_policy
        self._max_attempts = max_attempts
        self._retry_base_delay = retry_base_delay
        self._sleep = clock

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
        last_error = self._run_remediation(detection)
        if last_error is None:
            return  # at least one remediation resolver succeeded, or none configured

        # Remediation failed — carry forward notification error if no remediation error
        if last_error is last_notification_error:
            # Only notification resolvers existed and they all failed
            self._apply_failure_policy(detection, last_error, attempts=1)
            return

        # Apply failure policy for remediation failure
        self._apply_failure_policy(detection, last_error, attempts=1)

    def _run_remediation(self, detection: Detection) -> Exception | None:
        """Run remediation resolvers. Return None on success, last error on failure."""
        last_error: Exception | None = None
        for resolver in self._resolvers:
            if not resolver.is_notification:
                try:
                    resolver.resolve(detection)
                    logger.info("resolver_succeeded", resolver=resolver.name)
                    return None
                except Exception as e:
                    logger.warning("resolver_failed", resolver=resolver.name, error=str(e))
                    last_error = e
        return last_error

    def _run_webhook_resolvers(self, detection: Detection) -> Exception | None:
        """Run only escalation/webhook resolvers. Return None on success."""
        last_error: Exception | None = None
        for resolver in self._resolvers:
            if not resolver.is_notification and resolver.name == "escalate":
                try:
                    resolver.resolve(detection)
                    logger.info("resolver_succeeded", resolver=resolver.name)
                    return None
                except Exception as e:
                    logger.warning("resolver_failed", resolver=resolver.name, error=str(e))
                    last_error = e
        return last_error

    def _apply_failure_policy(
        self, detection: Detection, last_error: Exception, attempts: int
    ) -> None:
        policy = self._failure_policy

        if policy == ResolutionFailurePolicy.IGNORE:
            logger.warning(
                "resolution_failed_ignored",
                detection_type=detection.type.value,
                error=str(last_error),
            )
            return

        if policy == ResolutionFailurePolicy.MARK_UNRESOLVED:
            detection.resolution_exhausted = True
            logger.warning(
                "resolution_marked_unresolved",
                detection_type=detection.type.value,
                error=str(last_error),
            )
            return

        if policy == ResolutionFailurePolicy.RAISE:
            raise ResolutionExhaustedError(
                detection=detection,
                attempts=attempts,
                last_error=last_error,
            )

        if policy == ResolutionFailurePolicy.RETRY_WEBHOOK:
            self._retry(
                detection,
                run_fn=self._run_webhook_resolvers,
                last_error=last_error,
                label="retry_webhook",
            )
            return

        if policy == ResolutionFailurePolicy.RETRY_CHAIN:
            self._retry(
                detection,
                run_fn=self._run_remediation,
                last_error=last_error,
                label="retry_chain",
            )
            return

    def _retry(
        self,
        detection: Detection,
        run_fn: Callable[[Detection], Exception | None],
        last_error: Exception,
        label: str,
    ) -> None:
        """Retry ``run_fn`` up to ``max_attempts - 1`` more times with exponential backoff.

        The first attempt already happened in resolve(), so we start at attempt 2.
        """
        error = last_error
        for attempt in range(2, self._max_attempts + 1):
            delay = self._retry_base_delay * (2 ** (attempt - 2))
            logger.info(
                f"{label}_backoff",
                attempt=attempt,
                max_attempts=self._max_attempts,
                delay=delay,
            )
            self._sleep(delay)

            result = run_fn(detection)
            if result is None:
                logger.info(f"{label}_succeeded", attempt=attempt)
                return
            error = result

        # All retries exhausted
        detection.resolution_exhausted = True
        raise ResolutionExhaustedError(
            detection=detection,
            attempts=self._max_attempts,
            last_error=error,
        )
