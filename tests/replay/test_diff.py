# tests/replay/test_diff.py

from __future__ import annotations

from tangle.replay import diff_detections
from tangle.types import Cycle, Detection, DetectionType, LivelockPattern, Severity


def _deadlock(
    agents: list[str],
    wf: str = "wf",
    severity: Severity = Severity.CRITICAL,
) -> Detection:
    return Detection(
        type=DetectionType.DEADLOCK,
        severity=severity,
        cycle=Cycle(agents=list(agents), workflow_id=wf),
    )


def _livelock(agents: list[str], wf: str = "wf") -> Detection:
    return Detection(
        type=DetectionType.LIVELOCK,
        severity=Severity.CRITICAL,
        livelock=LivelockPattern(agents=list(agents), workflow_id=wf),
    )


def test_identical_runs_have_no_diff() -> None:
    original = [_deadlock(["A", "B"])]
    replayed = [_deadlock(["A", "B"])]
    diff = diff_detections(original, replayed)
    assert diff.is_identical
    assert not diff.is_regression
    assert diff.unchanged == 1


def test_missing_detection_is_a_regression() -> None:
    original = [_deadlock(["A", "B"])]
    replayed: list[Detection] = []
    diff = diff_detections(original, replayed)
    assert diff.is_regression
    assert len(diff.missing) == 1
    assert diff.missing[0]["agents"] == ["A", "B"]


def test_added_detection_is_not_a_regression() -> None:
    original: list[Detection] = []
    replayed = [_deadlock(["A", "B"])]
    diff = diff_detections(original, replayed)
    assert not diff.is_regression
    assert not diff.is_identical
    assert len(diff.added) == 1


def test_severity_change_is_a_regression() -> None:
    original = [_deadlock(["A", "B"], severity=Severity.CRITICAL)]
    replayed = [_deadlock(["A", "B"], severity=Severity.WARNING)]
    diff = diff_detections(original, replayed)
    assert diff.is_regression
    assert len(diff.changed) == 1
    assert diff.changed[0]["old_severity"] == "critical"
    assert diff.changed[0]["new_severity"] == "warning"


def test_agent_order_is_not_significant_for_matching() -> None:
    """Fingerprints sort agents, so A,B and B,A match as the same detection."""
    original = [_deadlock(["A", "B"])]
    replayed = [_deadlock(["B", "A"])]
    diff = diff_detections(original, replayed)
    assert diff.is_identical


def test_livelock_diff_matches_by_agents_and_workflow() -> None:
    original = [_livelock(["A", "B"], wf="wf-1"), _livelock(["C", "D"], wf="wf-2")]
    replayed = [_livelock(["A", "B"], wf="wf-1")]
    diff = diff_detections(original, replayed)
    assert diff.is_regression
    assert diff.unchanged == 1
    assert len(diff.missing) == 1
    assert diff.missing[0]["workflow_id"] == "wf-2"


def test_multiple_same_fingerprint_matched_by_multiplicity() -> None:
    original = [_deadlock(["A", "B"]), _deadlock(["A", "B"]), _deadlock(["A", "B"])]
    replayed = [_deadlock(["A", "B"])]
    diff = diff_detections(original, replayed)
    assert diff.unchanged == 1
    assert len(diff.missing) == 2


def test_format_produces_readable_output() -> None:
    original = [_deadlock(["A", "B"])]
    replayed: list[Detection] = []
    diff = diff_detections(original, replayed)
    text = diff.format()
    assert "missing  : 1" in text
    assert "deadlock" in text
