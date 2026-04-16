# tests/replay/test_bundle.py

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tangle.config import TangleConfig
from tangle.monitor import TangleMonitor
from tangle.replay import pack_bundle, replay_events, unpack_bundle
from tangle.replay.bundle import BUNDLE_FORMAT_VERSION, BundleManifest
from tangle.replay.log import LogCorruptionError
from tangle.types import Detection, DetectionType, Event, EventType

if TYPE_CHECKING:
    from pathlib import Path


def _record_log(tmp_path: Path) -> tuple[Path, list[Detection]]:
    log_path = tmp_path / "events.jsonl"
    config = TangleConfig(
        event_log_path=str(log_path),
        event_log_fsync=False,
        cycle_check_interval=10**9,
    )
    monitor = TangleMonitor(config=config)
    detections: list[Detection] = []
    try:
        events = [
            Event(EventType.REGISTER, 1.0, "wf", "A"),
            Event(EventType.REGISTER, 2.0, "wf", "B"),
            Event(EventType.WAIT_FOR, 3.0, "wf", "A", "B"),
            Event(EventType.WAIT_FOR, 4.0, "wf", "B", "A"),
        ]
        for ev in events:
            d = monitor.process_event(ev)
            if d is not None:
                detections.append(d)
    finally:
        monitor.stop()
    return log_path, detections


def test_bundle_roundtrip(tmp_path: Path) -> None:
    log_path, detections = _record_log(tmp_path)
    out = tmp_path / "incident.tgz"

    pack_bundle(
        out,
        events_log=log_path,
        detections=detections,
        config=TangleConfig(),
        note="customer ticket 42",
    )

    bundle = unpack_bundle(out)
    assert isinstance(bundle.manifest, BundleManifest)
    assert bundle.manifest.bundle_format == BUNDLE_FORMAT_VERSION
    assert bundle.manifest.note == "customer ticket 42"
    assert bundle.manifest.config  # non-empty

    assert len(bundle.events) == 4
    assert bundle.events[0].type == EventType.REGISTER
    assert len(bundle.detections) == 1
    assert bundle.detections[0].type == DetectionType.DEADLOCK


def test_bundle_replay_matches_recorded_detections(tmp_path: Path) -> None:
    log_path, detections = _record_log(tmp_path)
    out = tmp_path / "bundle.tgz"
    pack_bundle(out, events_log=log_path, detections=detections)

    bundle = unpack_bundle(out)
    config = TangleConfig(**bundle.manifest.config) if bundle.manifest.config else None
    result = replay_events(bundle.events, config=config)

    assert len(result.detections) == len(bundle.detections) == 1
    assert result.detections[0].type == DetectionType.DEADLOCK


def test_bundle_missing_log_raises(tmp_path: Path) -> None:
    out = tmp_path / "bundle.tgz"
    with pytest.raises(FileNotFoundError):
        pack_bundle(out, events_log=tmp_path / "does-not-exist.jsonl")


def test_bundle_rejects_tampered_log(tmp_path: Path) -> None:
    log_path, _ = _record_log(tmp_path)

    # Corrupt the last event line so its hash no longer matches.
    lines = log_path.read_text().splitlines()
    lines[-1] = lines[-1].replace('"timestamp":4.0', '"timestamp":99.0')
    log_path.write_text("\n".join(lines) + "\n")

    out = tmp_path / "bundle.tgz"
    pack_bundle(out, events_log=log_path, detections=[])

    with pytest.raises(LogCorruptionError):
        unpack_bundle(out)


def test_pack_bundle_preserves_log_bytes(tmp_path: Path) -> None:
    """Bundling must copy the log byte-for-byte so its hashes remain valid."""
    log_path, _ = _record_log(tmp_path)
    original_bytes = log_path.read_bytes()

    out = tmp_path / "bundle.tgz"
    pack_bundle(out, events_log=log_path, detections=[])

    import tarfile

    with tarfile.open(out, "r:gz") as tar:
        member = tar.getmember("events.jsonl")
        fh = tar.extractfile(member)
        assert fh is not None
        assert fh.read() == original_bytes


def test_support_bundle_detects_replay_regression(tmp_path: Path) -> None:
    """Simulate a detector regression: original detected a deadlock, replay doesn't."""
    from tangle.replay import diff_detections

    log_path, detections = _record_log(tmp_path)
    out = tmp_path / "bundle.tgz"
    pack_bundle(out, events_log=log_path, detections=detections)

    # Pretend the detector is fine — the diff should say identical.
    bundle = unpack_bundle(out)
    result = replay_events(bundle.events)
    diff = diff_detections(bundle.detections, result.detections)
    assert diff.is_identical

    # Now simulate a regression by dropping the replayed detection.
    diff = diff_detections(bundle.detections, [])
    assert diff.is_regression
    assert len(diff.missing) == 1
