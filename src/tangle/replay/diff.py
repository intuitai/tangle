# src/tangle/replay/diff.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tangle.types import Detection

# A detection fingerprint is a tuple that is stable across runs:
#   (detection type, workflow, sorted agent tuple)
# We deliberately exclude UUIDs and monotonic timestamps, which are guaranteed
# to differ between the original and a replay.
_Fingerprint = tuple[str, str, tuple[str, ...]]


def _fingerprint(detection: Detection) -> _Fingerprint:
    if detection.cycle is not None:
        return (
            detection.type.value,
            detection.cycle.workflow_id,
            tuple(sorted(detection.cycle.agents)),
        )
    if detection.livelock is not None:
        return (
            detection.type.value,
            detection.livelock.workflow_id,
            tuple(sorted(detection.livelock.agents)),
        )
    return (detection.type.value, "", ())


def _describe(detection: Detection) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": detection.type.value,
        "severity": detection.severity.value,
    }
    if detection.cycle is not None:
        out["workflow_id"] = detection.cycle.workflow_id
        out["agents"] = list(detection.cycle.agents)
        out["kind"] = "cycle"
    elif detection.livelock is not None:
        out["workflow_id"] = detection.livelock.workflow_id
        out["agents"] = list(detection.livelock.agents)
        out["pattern_length"] = detection.livelock.pattern_length
        out["repeat_count"] = detection.livelock.repeat_count
        out["kind"] = "livelock"
    return out


@dataclass(slots=True)
class DetectionDiff:
    """Per-fingerprint diff between an original run and a replay.

    ``missing`` — detection existed originally but didn't fire during replay
                   (a regression; the detector missed something it used to catch).
    ``added``   — detection fires now but didn't originally (either a new true
                   positive or a new false positive; human must decide).
    ``changed`` — same fingerprint both sides, but severity or type changed.
    ``unchanged`` — count of detections present in both runs with matching attrs.
    """

    missing: list[dict[str, Any]] = field(default_factory=list)
    added: list[dict[str, Any]] = field(default_factory=list)
    changed: list[dict[str, Any]] = field(default_factory=list)
    unchanged: int = 0

    @property
    def is_regression(self) -> bool:
        """True if the replay drops any detection the original caught."""
        return bool(self.missing) or bool(self.changed)

    @property
    def is_identical(self) -> bool:
        return not self.missing and not self.added and not self.changed

    def format(self) -> str:
        lines: list[str] = []
        lines.append(f"unchanged: {self.unchanged}")
        lines.append(f"missing  : {len(self.missing)}")
        for d in self.missing:
            wf = d.get("workflow_id", "")
            lines.append(f"  - {d['type']} wf={wf} agents={d.get('agents', [])}")
        lines.append(f"added    : {len(self.added)}")
        for d in self.added:
            wf = d.get("workflow_id", "")
            lines.append(f"  + {d['type']} wf={wf} agents={d.get('agents', [])}")
        lines.append(f"changed  : {len(self.changed)}")
        for d in self.changed:
            lines.append(
                f"  ~ {d['type']} wf={d.get('workflow_id', '')} "
                f"severity {d['old_severity']} -> {d['new_severity']}"
            )
        return "\n".join(lines)


def diff_detections(
    original: list[Detection],
    replayed: list[Detection],
) -> DetectionDiff:
    """Compare detections from the original run against a replay.

    Matching is by fingerprint. Multiple detections with the same fingerprint
    (which happens when livelock ringbuffers fire repeatedly) are matched by
    multiplicity: N in original + M in replay -> min(N,M) matches, remainder
    goes to missing/added.
    """
    orig_by_fp: dict[_Fingerprint, list[Detection]] = {}
    for d in original:
        orig_by_fp.setdefault(_fingerprint(d), []).append(d)
    repl_by_fp: dict[_Fingerprint, list[Detection]] = {}
    for d in replayed:
        repl_by_fp.setdefault(_fingerprint(d), []).append(d)

    diff = DetectionDiff()
    all_fps: set[_Fingerprint] = set(orig_by_fp) | set(repl_by_fp)

    for fp in all_fps:
        orig_list = orig_by_fp.get(fp, [])
        repl_list = repl_by_fp.get(fp, [])
        pair_count = min(len(orig_list), len(repl_list))

        for i in range(pair_count):
            o, r = orig_list[i], repl_list[i]
            if o.severity == r.severity and o.type == r.type:
                diff.unchanged += 1
            else:
                entry = _describe(o)
                entry["old_severity"] = o.severity.value
                entry["new_severity"] = r.severity.value
                diff.changed.append(entry)

        for o in orig_list[pair_count:]:
            diff.missing.append(_describe(o))
        for r in repl_list[pair_count:]:
            diff.added.append(_describe(r))

    # Stable ordering for human-readable output.
    diff.missing.sort(key=lambda d: (d.get("workflow_id", ""), d.get("type", "")))
    diff.added.sort(key=lambda d: (d.get("workflow_id", ""), d.get("type", "")))
    diff.changed.sort(key=lambda d: (d.get("workflow_id", ""), d.get("type", "")))
    return diff
