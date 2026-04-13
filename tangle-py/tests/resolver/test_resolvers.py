# tests/resolver/test_resolvers.py

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from tangle.graph.wfg import WaitForGraph
from tangle.resolver.alert import AlertResolver
from tangle.resolver.cancel import CancelResolver
from tangle.resolver.chain import ResolverChain
from tangle.resolver.escalate import EscalateResolver
from tangle.resolver.tiebreaker import TiebreakerResolver
from tangle.types import (Cycle, Detection, DetectionType, LivelockPattern,
                          ResolutionAction, Severity)
# Re-use conftest helpers
from tests.conftest import MockResolver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deadlock_detection(
    agents: list[str] | None = None, workflow_id: str = "wf-1"
) -> Detection:
    """Build a Detection with a Cycle for testing resolvers."""
    if agents is None:
        agents = ["A", "B", "C"]
    return Detection(
        type=DetectionType.DEADLOCK,
        severity=Severity.CRITICAL,
        cycle=Cycle(agents=agents, workflow_id=workflow_id),
    )


def _livelock_detection(
    agents: list[str] | None = None, workflow_id: str = "wf-1"
) -> Detection:
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
        resolver = CancelResolver(
            graph, cancel_fn=cancel_fn, mode=ResolutionAction.CANCEL_YOUNGEST
        )
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
        resolver = CancelResolver(
            graph, cancel_fn=cancel_fn, mode=ResolutionAction.CANCEL_ALL
        )
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
        resolver = CancelResolver(
            graph, cancel_fn=cancel_fn, mode=ResolutionAction.CANCEL_YOUNGEST
        )
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

        with patch(
            "httpx.post",
            side_effect=httpx.TimeoutException("timed out"),
        ), pytest.raises(httpx.TimeoutException):
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

    def test_chain_resolver_all_fail(self) -> None:
        """When all resolvers fail, the last error is raised."""
        first = MockResolver()
        first.should_fail = True
        second = MockResolver()
        second.should_fail = True
        chain = ResolverChain(resolvers=[first, second])
        detection = _deadlock_detection()

        with pytest.raises(RuntimeError, match="MockResolver forced failure"):
            chain.resolve(detection)

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

    def test_chain_single_resolver_failure(self) -> None:
        """Single failing resolver raises its error."""
        only = MockResolver()
        only.should_fail = True
        chain = ResolverChain(resolvers=[only])
        detection = _deadlock_detection()

        with pytest.raises(RuntimeError, match="MockResolver forced failure"):
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
        resolver = CancelResolver(
            graph, cancel_fn=cancel_fn, mode=ResolutionAction.CANCEL_YOUNGEST
        )
        detection = _deadlock_detection(["X", "Y", "Z"])

        resolver.resolve(detection)

        cancel_fn.assert_called_once()
        canceled = cancel_fn.call_args[0][0]
        assert canceled == "Z"  # last in list

    def test_cancel_with_empty_agents(self) -> None:
        """Detection with neither cycle nor livelock results in no cancel calls."""
        graph = WaitForGraph()
        cancel_fn = MagicMock()
        resolver = CancelResolver(
            graph, cancel_fn=cancel_fn, mode=ResolutionAction.CANCEL_YOUNGEST
        )
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

        with patch.dict(
            os.environ, {"TANGLE_ESCALATION_WEBHOOK_TOKEN": "secret-token"}
        ), patch("httpx.post", return_value=mock_response) as mock_post:
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
