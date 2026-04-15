# tests/resolver/test_resolvers.py

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from tangle.graph.wfg import WaitForGraph
from tangle.resolver.alert import AlertResolver
from tangle.resolver.cancel import CancelResolver
from tangle.resolver.chain import ResolverChain
from tangle.resolver.errors import ResolutionExhaustedError
from tangle.resolver.escalate import EscalateResolver
from tangle.resolver.tiebreaker import TiebreakerResolver
from tangle.types import (
    Cycle,
    Detection,
    DetectionType,
    LivelockPattern,
    ResolutionAction,
    ResolutionFailurePolicy,
    Severity,
)

# Re-use conftest helpers
from tests.conftest import MockResolver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deadlock_detection(agents: list[str] | None = None, workflow_id: str = "wf-1") -> Detection:
    """Build a Detection with a Cycle for testing resolvers."""
    if agents is None:
        agents = ["A", "B", "C"]
    return Detection(
        type=DetectionType.DEADLOCK,
        severity=Severity.CRITICAL,
        cycle=Cycle(agents=agents, workflow_id=workflow_id),
    )


def _livelock_detection(agents: list[str] | None = None, workflow_id: str = "wf-1") -> Detection:
    """Build a Detection with a LivelockPattern for testing resolvers."""
    if agents is None:
        agents = ["A", "B"]
    return Detection(
        type=DetectionType.LIVELOCK,
        severity=Severity.WARNING,
        livelock=LivelockPattern(
            agents=agents, workflow_id=workflow_id, pattern_length=2, repeat_count=3
        ),
    )


# ===========================================================================
# AlertResolver
# ===========================================================================


class TestAlertResolver:
    def test_alert_resolver_calls_callback(self) -> None:
        """The on_detection callback is invoked when resolve() is called."""
        callback = MagicMock()
        resolver = AlertResolver(on_detection=callback)
        detection = _deadlock_detection()

        resolver.resolve(detection)

        callback.assert_called_once_with(detection)

    def test_alert_resolver_no_callback(self) -> None:
        """Resolver does not crash when no callback is provided."""
        resolver = AlertResolver(on_detection=None)
        detection = _deadlock_detection()
        # Should not raise
        resolver.resolve(detection)

    def test_alert_resolver_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        """Resolver emits a structlog warning log message."""
        import structlog

        # Capture structlog output via stdlib logging
        structlog.configure(
            processors=[
                structlog.stdlib.add_log_level,
                structlog.dev.ConsoleRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            logger_factory=structlog.stdlib.LoggerFactory(),
        )

        resolver = AlertResolver()
        detection = _deadlock_detection()

        with caplog.at_level("WARNING"):
            resolver.resolve(detection)

        # Structlog should have produced at least one warning-level record
        assert len(caplog.records) >= 1
        # Re-configure structlog back to defaults to avoid polluting other tests
        structlog.reset_defaults()

    def test_alert_resolver_name(self) -> None:
        """Resolver has the expected name property."""
        resolver = AlertResolver()
        assert resolver.name == "alert"


# ===========================================================================
# CancelResolver
# ===========================================================================


class TestCancelResolver:
    def test_cancel_resolver_youngest(self) -> None:
        """In CANCEL_YOUNGEST mode, the agent with the latest join time is canceled."""
        graph = WaitForGraph()
        graph.register_agent("A", "wf-1", 1.0)
        graph.register_agent("B", "wf-1", 2.0)
        graph.register_agent("C", "wf-1", 3.0)  # Youngest

        cancel_fn = MagicMock()
        resolver = CancelResolver(graph, cancel_fn=cancel_fn, mode=ResolutionAction.CANCEL_YOUNGEST)
        detection = _deadlock_detection(["A", "B", "C"])

        resolver.resolve(detection)

        cancel_fn.assert_called_once()
        canceled_agent = cancel_fn.call_args[0][0]
        assert canceled_agent == "C"

    def test_cancel_resolver_all(self) -> None:
        """In CANCEL_ALL mode, all agents in the cycle are canceled."""
        graph = WaitForGraph()
        graph.register_agent("A", "wf-1", 1.0)
        graph.register_agent("B", "wf-1", 2.0)
        graph.register_agent("C", "wf-1", 3.0)

        cancel_fn = MagicMock()
        resolver = CancelResolver(graph, cancel_fn=cancel_fn, mode=ResolutionAction.CANCEL_ALL)
        detection = _deadlock_detection(["A", "B", "C"])

        resolver.resolve(detection)

        assert cancel_fn.call_count == 3
        canceled_agents = {call[0][0] for call in cancel_fn.call_args_list}
        assert canceled_agents == {"A", "B", "C"}

    def test_cancel_resolver_nil_fn(self) -> None:
        """When cancel_fn is None, resolve() gracefully skips without error."""
        graph = WaitForGraph()
        graph.register_agent("A", "wf-1", 1.0)

        resolver = CancelResolver(graph, cancel_fn=None)
        detection = _deadlock_detection(["A"])

        # Should not raise
        resolver.resolve(detection)

    def test_cancel_resolver_name(self) -> None:
        """Resolver has the expected name property."""
        graph = WaitForGraph()
        resolver = CancelResolver(graph)
        assert resolver.name == "cancel"

    def test_cancel_resolver_livelock(self) -> None:
        """CancelResolver works with livelock detections (uses livelock.agents)."""
        graph = WaitForGraph()
        graph.register_agent("A", "wf-1", 1.0)
        graph.register_agent("B", "wf-1", 5.0)  # Youngest

        cancel_fn = MagicMock()
        resolver = CancelResolver(graph, cancel_fn=cancel_fn, mode=ResolutionAction.CANCEL_YOUNGEST)
        detection = _livelock_detection(["A", "B"])

        resolver.resolve(detection)

        cancel_fn.assert_called_once()
        canceled_agent = cancel_fn.call_args[0][0]
        assert canceled_agent == "B"


# ===========================================================================
# TiebreakerResolver
# ===========================================================================


class TestTiebreakerResolver:
    def test_tiebreaker_resolver(self) -> None:
        """tiebreaker_fn is called with the first agent and the configured prompt."""
        tiebreaker_fn = MagicMock()
        prompt = "Stop looping!"
        resolver = TiebreakerResolver(tiebreaker_fn=tiebreaker_fn, prompt=prompt)
        detection = _deadlock_detection(["A", "B", "C"])

        resolver.resolve(detection)

        tiebreaker_fn.assert_called_once_with("A", prompt)

    def test_tiebreaker_resolver_no_fn(self) -> None:
        """When tiebreaker_fn is None, resolve() gracefully skips."""
        resolver = TiebreakerResolver(tiebreaker_fn=None)
        detection = _deadlock_detection(["A", "B"])
        # Should not raise
        resolver.resolve(detection)

    def test_tiebreaker_resolver_default_prompt(self) -> None:
        """The default prompt is used when no custom prompt is given."""
        tiebreaker_fn = MagicMock()
        resolver = TiebreakerResolver(tiebreaker_fn=tiebreaker_fn)
        detection = _deadlock_detection(["X"])

        resolver.resolve(detection)

        _, call_prompt = tiebreaker_fn.call_args[0]
        assert "loop" in call_prompt.lower() or "stuck" in call_prompt.lower()

    def test_tiebreaker_resolver_name(self) -> None:
        """Resolver has the expected name property."""
        resolver = TiebreakerResolver()
        assert resolver.name == "tiebreaker"


# ===========================================================================
# EscalateResolver
# ===========================================================================


class TestEscalateResolver:
    def test_escalate_resolver_success(self) -> None:
        """Mock httpx POST returning 200 succeeds without error."""
        resolver = EscalateResolver(webhook_url="https://example.com/hook")
        detection = _deadlock_detection(["A", "B"])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response) as mock_post:
            resolver.resolve(detection)

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["json"]["type"] == "deadlock"
        assert call_kwargs[1]["json"]["severity"] == "critical"

    def test_escalate_resolver_failure(self) -> None:
        """Mock httpx POST returning 500 raises HTTPStatusError."""
        resolver = EscalateResolver(webhook_url="https://example.com/hook")
        detection = _deadlock_detection(["A"])

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "Server Error",
                request=MagicMock(),
                response=mock_response,
            )
        )

        with (
            patch("httpx.post", return_value=mock_response),
            pytest.raises(httpx.HTTPStatusError),
        ):
            resolver.resolve(detection)

    def test_escalate_resolver_timeout(self) -> None:
        """Mock httpx timeout raises TimeoutException."""
        resolver = EscalateResolver(webhook_url="https://example.com/hook")
        detection = _deadlock_detection(["A"])

        with (
            patch(
                "httpx.post",
                side_effect=httpx.TimeoutException("timed out"),
            ),
            pytest.raises(httpx.TimeoutException),
        ):
            resolver.resolve(detection)

    def test_escalate_resolver_no_url(self) -> None:
        """When no webhook_url is configured, resolve() skips silently."""
        resolver = EscalateResolver(webhook_url="")
        detection = _deadlock_detection(["A"])

        with patch("httpx.post") as mock_post:
            resolver.resolve(detection)

        mock_post.assert_not_called()

    def test_escalate_resolver_livelock_payload(self) -> None:
        """Livelock detection includes livelock fields in the payload."""
        resolver = EscalateResolver(webhook_url="https://example.com/hook")
        detection = _livelock_detection(["A", "B"])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response) as mock_post:
            resolver.resolve(detection)

        payload = mock_post.call_args[1]["json"]
        assert "livelock" in payload
        assert payload["livelock"]["pattern_length"] == 2
        assert payload["livelock"]["repeat_count"] == 3

    def test_escalate_resolver_name(self) -> None:
        """Resolver has the expected name property."""
        resolver = EscalateResolver()
        assert resolver.name == "escalate"


# ===========================================================================
# ResolverChain
# ===========================================================================


class TestResolverChain:
    def test_chain_resolver_first_succeeds(self) -> None:
        """When the first resolver succeeds, the second is not called."""
        first = MockResolver()
        second = MockResolver()
        chain = ResolverChain(resolvers=[first, second])
        detection = _deadlock_detection()

        chain.resolve(detection)

        assert first.count == 1
        assert second.count == 0

    def test_chain_resolver_fallback(self) -> None:
        """When the first resolver fails, the chain falls back to the second."""
        first = MockResolver()
        first.should_fail = True
        second = MockResolver()
        chain = ResolverChain(resolvers=[first, second])
        detection = _deadlock_detection()

        chain.resolve(detection)

        assert first.count == 1  # detection appended before resolver raised
        assert second.count == 1

    def test_chain_resolver_all_fail_ignore_policy(self) -> None:
        """Default IGNORE policy: when all resolvers fail, no exception is raised."""
        first = MockResolver()
        first.should_fail = True
        second = MockResolver()
        second.should_fail = True
        chain = ResolverChain(resolvers=[first, second])
        detection = _deadlock_detection()

        # Should not raise with default IGNORE policy
        chain.resolve(detection)

    def test_chain_resolver_all_fail_raise_policy(self) -> None:
        """RAISE policy: when all resolvers fail, ResolutionExhaustedError is raised."""
        first = MockResolver()
        first.should_fail = True
        second = MockResolver()
        second.should_fail = True
        chain = ResolverChain(
            resolvers=[first, second],
            failure_policy=ResolutionFailurePolicy.RAISE,
        )
        detection = _deadlock_detection()

        with pytest.raises(ResolutionExhaustedError) as exc_info:
            chain.resolve(detection)

        assert exc_info.value.attempts == 1
        assert isinstance(exc_info.value.last_error, RuntimeError)

    def test_chain_resolver_empty(self) -> None:
        """An empty chain does not raise."""
        chain = ResolverChain(resolvers=[])
        detection = _deadlock_detection()
        # Should not raise
        chain.resolve(detection)

    def test_chain_resolver_add(self) -> None:
        """Resolvers can be added via the add() method."""
        chain = ResolverChain()
        resolver = MockResolver()
        chain.add(resolver)
        detection = _deadlock_detection()

        chain.resolve(detection)

        assert resolver.count == 1

    def test_chain_resolver_name(self) -> None:
        """Chain has the expected name property."""
        chain = ResolverChain()
        assert chain.name == "chain"

    def test_chain_three_resolvers_stops_on_second(self) -> None:
        """First fails, second succeeds, third is not called."""
        first = MockResolver()
        first.should_fail = True
        second = MockResolver()
        third = MockResolver()
        chain = ResolverChain(resolvers=[first, second, third])
        detection = _deadlock_detection()

        chain.resolve(detection)

        assert first.count == 1  # detection appended before resolver raised
        assert second.count == 1
        assert third.count == 0

    def test_chain_single_resolver_failure_ignore(self) -> None:
        """Single failing resolver with IGNORE policy does not raise."""
        only = MockResolver()
        only.should_fail = True
        chain = ResolverChain(resolvers=[only])
        detection = _deadlock_detection()

        # Should not raise with default IGNORE policy
        chain.resolve(detection)


# ===========================================================================
# CancelResolver edge cases
# ===========================================================================


class TestCancelResolverEdgeCases:
    def test_find_youngest_fallback_no_join_times(self) -> None:
        """When no agent has a join time, falls back to agents[-1]."""
        graph = WaitForGraph()
        # Don't register agents — no join times available
        cancel_fn = MagicMock()
        resolver = CancelResolver(graph, cancel_fn=cancel_fn, mode=ResolutionAction.CANCEL_YOUNGEST)
        detection = _deadlock_detection(["X", "Y", "Z"])

        resolver.resolve(detection)

        cancel_fn.assert_called_once()
        canceled = cancel_fn.call_args[0][0]
        assert canceled == "Z"  # last in list

    def test_cancel_with_empty_agents(self) -> None:
        """Detection with neither cycle nor livelock results in no cancel calls."""
        graph = WaitForGraph()
        cancel_fn = MagicMock()
        resolver = CancelResolver(graph, cancel_fn=cancel_fn, mode=ResolutionAction.CANCEL_YOUNGEST)
        detection = Detection(
            type=DetectionType.DEADLOCK,
            severity=Severity.CRITICAL,
            cycle=None,
            livelock=None,
        )

        resolver.resolve(detection)

        cancel_fn.assert_not_called()


# ===========================================================================
# TiebreakerResolver edge cases
# ===========================================================================


class TestTiebreakerResolverEdgeCases:
    def test_tiebreaker_with_livelock(self) -> None:
        """TiebreakerResolver works with livelock detections."""
        tiebreaker_fn = MagicMock()
        resolver = TiebreakerResolver(tiebreaker_fn=tiebreaker_fn, prompt="break")
        detection = _livelock_detection(["P", "Q"])

        resolver.resolve(detection)

        tiebreaker_fn.assert_called_once_with("P", "break")

    def test_tiebreaker_empty_agents(self) -> None:
        """Tiebreaker with empty agents list does not call fn."""
        tiebreaker_fn = MagicMock()
        resolver = TiebreakerResolver(tiebreaker_fn=tiebreaker_fn)
        detection = Detection(
            type=DetectionType.DEADLOCK,
            severity=Severity.CRITICAL,
            cycle=Cycle(agents=[]),
        )

        resolver.resolve(detection)

        tiebreaker_fn.assert_not_called()


# ===========================================================================
# EscalateResolver edge cases
# ===========================================================================


class TestEscalateResolverEdgeCases:
    def test_escalate_bearer_token(self) -> None:
        """TANGLE_ESCALATION_WEBHOOK_TOKEN env var adds Authorization header."""
        import os

        resolver = EscalateResolver(webhook_url="https://example.com/hook")
        detection = _deadlock_detection(["A"])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with (
            patch.dict(os.environ, {"TANGLE_ESCALATION_WEBHOOK_TOKEN": "secret-token"}),
            patch("httpx.post", return_value=mock_response) as mock_post,
        ):
            resolver.resolve(detection)

        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer secret-token"

    def test_escalate_cycle_payload_fields(self) -> None:
        """Escalation payload for cycle includes id, agents, workflow_id."""
        resolver = EscalateResolver(webhook_url="https://example.com/hook")
        detection = _deadlock_detection(["A", "B"], workflow_id="wf-test")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response) as mock_post:
            resolver.resolve(detection)

        payload = mock_post.call_args[1]["json"]
        assert "cycle" in payload
        assert payload["cycle"]["agents"] == ["A", "B"]
        assert payload["cycle"]["workflow_id"] == "wf-test"
        assert "id" in payload["cycle"]

    def test_escalate_connection_error(self) -> None:
        """Connection error propagates (not caught by resolver)."""
        resolver = EscalateResolver(webhook_url="https://example.com/hook")
        detection = _deadlock_detection(["A"])

        with (
            patch("httpx.post", side_effect=httpx.ConnectError("connection refused")),
            pytest.raises(httpx.ConnectError),
        ):
            resolver.resolve(detection)


# ===========================================================================
# Resolution failure policies
# ===========================================================================


class _MockEscalateResolver:
    """A fake escalation resolver for testing retry_webhook policy."""

    def __init__(self) -> None:
        self.call_count = 0
        self.succeed_on: int | None = None  # succeed on Nth call (1-based)

    @property
    def name(self) -> str:
        return "escalate"

    @property
    def is_notification(self) -> bool:
        return False

    def resolve(self, detection: Detection) -> None:
        self.call_count += 1
        if self.succeed_on is not None and self.call_count >= self.succeed_on:
            return
        raise RuntimeError("webhook failed")


class TestResolutionFailurePolicy:
    def test_ignore_policy_swallows_error(self) -> None:
        """IGNORE policy logs the failure and returns without raising."""
        r = MockResolver()
        r.should_fail = True
        chain = ResolverChain(
            resolvers=[r],
            failure_policy=ResolutionFailurePolicy.IGNORE,
        )
        detection = _deadlock_detection()

        chain.resolve(detection)  # should not raise
        assert not detection.resolution_exhausted

    def test_raise_policy_raises_exhausted_error(self) -> None:
        """RAISE policy raises ResolutionExhaustedError with metadata."""
        r = MockResolver()
        r.should_fail = True
        chain = ResolverChain(
            resolvers=[r],
            failure_policy=ResolutionFailurePolicy.RAISE,
        )
        detection = _deadlock_detection()

        with pytest.raises(ResolutionExhaustedError) as exc_info:
            chain.resolve(detection)

        err = exc_info.value
        assert err.detection is detection
        assert err.attempts == 1
        assert isinstance(err.last_error, RuntimeError)
        assert "exhausted" in str(err).lower()

    def test_mark_unresolved_sets_flag(self) -> None:
        """MARK_UNRESOLVED policy sets detection.resolution_exhausted = True."""
        r = MockResolver()
        r.should_fail = True
        chain = ResolverChain(
            resolvers=[r],
            failure_policy=ResolutionFailurePolicy.MARK_UNRESOLVED,
        )
        detection = _deadlock_detection()

        chain.resolve(detection)  # should not raise

        assert detection.resolution_exhausted is True

    def test_retry_webhook_succeeds_on_second_attempt(self) -> None:
        """RETRY_WEBHOOK retries only escalation resolvers with backoff."""
        escalate = _MockEscalateResolver()
        escalate.succeed_on = 2  # fail first, succeed second
        delays: list[float] = []

        chain = ResolverChain(
            resolvers=[escalate],
            failure_policy=ResolutionFailurePolicy.RETRY_WEBHOOK,
            max_attempts=3,
            retry_base_delay=0.5,
            clock=lambda d: delays.append(d),
        )
        detection = _deadlock_detection()

        chain.resolve(detection)

        assert escalate.call_count == 2
        assert len(delays) == 1
        assert delays[0] == 0.5  # base_delay * 2^0

    def test_retry_webhook_exhausted(self) -> None:
        """RETRY_WEBHOOK raises ResolutionExhaustedError after max attempts."""
        escalate = _MockEscalateResolver()  # always fails

        chain = ResolverChain(
            resolvers=[escalate],
            failure_policy=ResolutionFailurePolicy.RETRY_WEBHOOK,
            max_attempts=3,
            retry_base_delay=0.01,
            clock=lambda _: None,  # no-op sleep for speed
        )
        detection = _deadlock_detection()

        with pytest.raises(ResolutionExhaustedError) as exc_info:
            chain.resolve(detection)

        assert exc_info.value.attempts == 3
        assert detection.resolution_exhausted is True
        assert escalate.call_count == 3  # 1 initial + 2 retries

    def test_retry_chain_succeeds_on_third_attempt(self) -> None:
        """RETRY_CHAIN retries the entire remediation phase."""
        r = MockResolver()
        r.should_fail = True  # always fails

        second = _MockEscalateResolver()
        second.succeed_on = 3  # succeed on third call

        delays: list[float] = []

        chain = ResolverChain(
            resolvers=[r, second],
            failure_policy=ResolutionFailurePolicy.RETRY_CHAIN,
            max_attempts=3,
            retry_base_delay=0.1,
            clock=lambda d: delays.append(d),
        )
        detection = _deadlock_detection()

        chain.resolve(detection)

        # first attempt: r fails, second fails (call 1)
        # retry 1: r fails, second fails (call 2)
        # retry 2: r fails, second succeeds (call 3)
        assert second.call_count == 3
        assert len(delays) == 2
        assert delays[0] == pytest.approx(0.1)  # 0.1 * 2^0
        assert delays[1] == pytest.approx(0.2)  # 0.1 * 2^1

    def test_retry_chain_exhausted(self) -> None:
        """RETRY_CHAIN raises after all attempts fail."""
        r = MockResolver()
        r.should_fail = True

        chain = ResolverChain(
            resolvers=[r],
            failure_policy=ResolutionFailurePolicy.RETRY_CHAIN,
            max_attempts=2,
            retry_base_delay=0.01,
            clock=lambda _: None,
        )
        detection = _deadlock_detection()

        with pytest.raises(ResolutionExhaustedError) as exc_info:
            chain.resolve(detection)

        assert exc_info.value.attempts == 2
        assert detection.resolution_exhausted is True

    def test_retry_backoff_is_exponential(self) -> None:
        """Verify delays follow exponential backoff: base * 2^(attempt-2)."""
        escalate = _MockEscalateResolver()  # always fails
        delays: list[float] = []

        chain = ResolverChain(
            resolvers=[escalate],
            failure_policy=ResolutionFailurePolicy.RETRY_WEBHOOK,
            max_attempts=4,
            retry_base_delay=1.0,
            clock=lambda d: delays.append(d),
        )
        detection = _deadlock_detection()

        with pytest.raises(ResolutionExhaustedError):
            chain.resolve(detection)

        assert delays == [1.0, 2.0, 4.0]  # 1*2^0, 1*2^1, 1*2^2

    def test_no_remediation_resolvers_no_failure(self) -> None:
        """With only notification resolvers, no failure policy is triggered."""
        alert = AlertResolver()
        chain = ResolverChain(
            resolvers=[alert],
            failure_policy=ResolutionFailurePolicy.RAISE,
        )
        detection = _deadlock_detection()

        # Notification-only chain should not trigger failure policy
        chain.resolve(detection)

    def test_retry_webhook_skips_non_escalate_resolvers(self) -> None:
        """RETRY_WEBHOOK only retries resolvers named 'escalate'."""
        regular = MockResolver()
        regular.should_fail = True

        escalate = _MockEscalateResolver()
        escalate.succeed_on = 2

        chain = ResolverChain(
            resolvers=[regular, escalate],
            failure_policy=ResolutionFailurePolicy.RETRY_WEBHOOK,
            max_attempts=3,
            retry_base_delay=0.01,
            clock=lambda _: None,
        )
        detection = _deadlock_detection()

        chain.resolve(detection)

        # regular resolver called once in initial pass, not during retry
        assert regular.count == 1
        # escalate called once in initial pass + once in retry
        assert escalate.call_count == 2
