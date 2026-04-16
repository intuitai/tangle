# src/tangle/replay/bundle.py

from __future__ import annotations

import io
import json
import platform
import sys
import tarfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tangle import __version__ as _tangle_version
from tangle.replay.log import (
    LOG_SCHEMA_VERSION,
    EventLogReader,
    _stable_json,
    decode_detection,
    encode_detection,
)

if TYPE_CHECKING:
    from tangle.config import TangleConfig
    from tangle.types import Detection, Event

BUNDLE_MANIFEST_NAME = "manifest.json"
BUNDLE_EVENTS_NAME = "events.jsonl"
BUNDLE_DETECTIONS_NAME = "detections.jsonl"
BUNDLE_FORMAT_VERSION = 1


@dataclass(slots=True)
class BundleManifest:
    """Metadata shipped with a support bundle.

    The manifest pins the tangle version and config used when the bundle was
    captured. When replay results diverge, this is the first thing a support
    engineer checks — did the detector code change, or did the config change?
    """

    tangle_version: str = _tangle_version
    bundle_format: int = BUNDLE_FORMAT_VERSION
    log_schema: int = LOG_SCHEMA_VERSION
    created_at: float = field(default_factory=time.time)
    python_version: str = field(default_factory=lambda: sys.version.split()[0])
    platform: str = field(default_factory=platform.platform)
    config: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> BundleManifest:
        data = json.loads(raw)
        return cls(**data)


def _add_bytes(tar: tarfile.TarFile, name: str, payload: bytes, mtime: float) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(payload)
    info.mtime = int(mtime)
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(payload))


def pack_bundle(
    output_path: str | Path,
    events_log: str | Path,
    detections: list[Detection] | None = None,
    config: TangleConfig | None = None,
    note: str = "",
) -> Path:
    """Write a gzipped tar containing the event log, detection history, and manifest.

    The event log is copied in verbatim so its integrity hashes stay valid.
    Detections are re-serialized to JSONL.
    """
    events_log_path = Path(events_log)
    if not events_log_path.exists():
        raise FileNotFoundError(f"event log not found: {events_log_path}")

    manifest = BundleManifest(
        config=(config.model_dump(mode="json") if config is not None else {}),
        note=note,
    )

    detection_lines: list[str] = []
    for d in detections or []:
        detection_lines.append(_stable_json({"detection": encode_detection(d)}))
    detections_blob = ("\n".join(detection_lines) + ("\n" if detection_lines else "")).encode(
        "utf-8"
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mtime = manifest.created_at
    with tarfile.open(output_path, mode="w:gz") as tar:
        _add_bytes(tar, BUNDLE_MANIFEST_NAME, manifest.to_json().encode("utf-8"), mtime)
        _add_bytes(tar, BUNDLE_EVENTS_NAME, events_log_path.read_bytes(), mtime)
        _add_bytes(tar, BUNDLE_DETECTIONS_NAME, detections_blob, mtime)
    return output_path


@dataclass(slots=True)
class UnpackedBundle:
    manifest: BundleManifest
    events: list[Event]
    detections: list[Detection]


def unpack_bundle(bundle_path: str | Path) -> UnpackedBundle:
    """Read a bundle and return the manifest, events, and recorded detections.

    The event log is parsed through EventLogReader, so hash/sequence validation
    runs during unpack — a tampered or truncated log raises LogCorruptionError.
    """
    bundle_path = Path(bundle_path)
    with tarfile.open(bundle_path, mode="r:gz") as tar:
        manifest = _read_manifest(tar)
        events_bytes = _read_member(tar, BUNDLE_EVENTS_NAME)
        detections_bytes = _read_member(tar, BUNDLE_DETECTIONS_NAME)

    # EventLogReader needs a path. Stage the log in a temp file so we can reuse
    # the same integrity-checking reader that live logs use.
    import tempfile

    with tempfile.NamedTemporaryFile("wb", delete=False, suffix=".jsonl") as tmp:
        tmp.write(events_bytes)
        tmp_path = Path(tmp.name)
    try:
        events = list(EventLogReader(tmp_path))
    finally:
        tmp_path.unlink(missing_ok=True)

    detections: list[Detection] = []
    for line in detections_bytes.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        detections.append(decode_detection(rec["detection"]))

    return UnpackedBundle(manifest=manifest, events=events, detections=detections)


def _read_member(tar: tarfile.TarFile, name: str) -> bytes:
    member = tar.getmember(name)
    fh = tar.extractfile(member)
    if fh is None:
        raise ValueError(f"member {name} has no contents")
    try:
        return fh.read()
    finally:
        fh.close()


def _read_manifest(tar: tarfile.TarFile) -> BundleManifest:
    raw = _read_member(tar, BUNDLE_MANIFEST_NAME).decode("utf-8")
    return BundleManifest.from_json(raw)
