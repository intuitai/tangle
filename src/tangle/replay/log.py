# src/tangle/replay/log.py

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tangle.types import (
    Cycle,
    Detection,
    DetectionType,
    Edge,
    Event,
    EventType,
    LivelockPattern,
    ResolutionAction,
    Severity,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

LOG_SCHEMA_VERSION = 1


class LogCorruptionError(Exception):
    """Raised when a log line fails hash or schema validation."""


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _line_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def encode_event(event: Event) -> dict[str, Any]:
    """Serialize an Event to a JSON-safe dict. Message bodies are base64-encoded."""
    return {
        "type": event.type.value,
        "timestamp": event.timestamp,
        "workflow_id": event.workflow_id,
        "from_agent": event.from_agent,
        "to_agent": event.to_agent,
        "resource": event.resource,
        "message_body_b64": base64.b64encode(event.message_body).decode("ascii"),
        "metadata": dict(event.metadata),
    }


def decode_event(data: dict[str, Any]) -> Event:
    return Event(
        type=EventType(data["type"]),
        timestamp=float(data["timestamp"]),
        workflow_id=data["workflow_id"],
        from_agent=data["from_agent"],
        to_agent=data.get("to_agent", ""),
        resource=data.get("resource", ""),
        message_body=base64.b64decode(data.get("message_body_b64", "")),
        metadata=dict(data.get("metadata", {})),
    )


def _encode_edge(edge: Edge) -> dict[str, Any]:
    return {
        "from_agent": edge.from_agent,
        "to_agent": edge.to_agent,
        "resource": edge.resource,
        "created_at": edge.created_at,
        "workflow_id": edge.workflow_id,
    }


def _decode_edge(data: dict[str, Any]) -> Edge:
    return Edge(
        from_agent=data["from_agent"],
        to_agent=data["to_agent"],
        resource=data.get("resource", ""),
        created_at=float(data["created_at"]),
        workflow_id=data.get("workflow_id", ""),
    )


def _encode_cycle(cycle: Cycle) -> dict[str, Any]:
    return {
        "id": cycle.id,
        "detected_at": cycle.detected_at,
        "agents": list(cycle.agents),
        "edges": [_encode_edge(e) for e in cycle.edges],
        "workflow_id": cycle.workflow_id,
        "resolved": cycle.resolved,
        "resolution": cycle.resolution.value if cycle.resolution else None,
    }


def _decode_cycle(data: dict[str, Any]) -> Cycle:
    resolution = data.get("resolution")
    return Cycle(
        id=data["id"],
        detected_at=float(data["detected_at"]),
        agents=list(data.get("agents", [])),
        edges=[_decode_edge(e) for e in data.get("edges", [])],
        workflow_id=data.get("workflow_id", ""),
        resolved=bool(data.get("resolved", False)),
        resolution=ResolutionAction(resolution) if resolution else None,
    )


def _encode_livelock(pattern: LivelockPattern) -> dict[str, Any]:
    return {
        "id": pattern.id,
        "detected_at": pattern.detected_at,
        "agents": list(pattern.agents),
        "pattern_length": pattern.pattern_length,
        "repeat_count": pattern.repeat_count,
        "workflow_id": pattern.workflow_id,
        "resolved": pattern.resolved,
        "resolution": pattern.resolution.value if pattern.resolution else None,
    }


def _decode_livelock(data: dict[str, Any]) -> LivelockPattern:
    resolution = data.get("resolution")
    return LivelockPattern(
        id=data["id"],
        detected_at=float(data["detected_at"]),
        agents=list(data.get("agents", [])),
        pattern_length=int(data.get("pattern_length", 0)),
        repeat_count=int(data.get("repeat_count", 0)),
        workflow_id=data.get("workflow_id", ""),
        resolved=bool(data.get("resolved", False)),
        resolution=ResolutionAction(resolution) if resolution else None,
    )


def encode_detection(detection: Detection) -> dict[str, Any]:
    return {
        "type": detection.type.value,
        "severity": detection.severity.value,
        "cycle": _encode_cycle(detection.cycle) if detection.cycle else None,
        "livelock": _encode_livelock(detection.livelock) if detection.livelock else None,
        "resolution_exhausted": detection.resolution_exhausted,
    }


def decode_detection(data: dict[str, Any]) -> Detection:
    return Detection(
        type=DetectionType(data["type"]),
        severity=Severity(data["severity"]),
        cycle=_decode_cycle(data["cycle"]) if data.get("cycle") else None,
        livelock=_decode_livelock(data["livelock"]) if data.get("livelock") else None,
        resolution_exhausted=bool(data.get("resolution_exhausted", False)),
    )


class EventLogWriter:
    """Append-only JSONL writer for Events.

    Format: first line is a header ``{"kind":"header","schema":1,...}``.
    Each subsequent line is ``{"kind":"event","seq":N,"hash":<16hex>,"event":{...}}``
    where ``hash`` is a truncated sha256 over the stable-JSON of the event payload.
    The file is flushed+fsynced after every append so a crash leaves a valid prefix.
    """

    def __init__(self, path: str | os.PathLike[str], *, fsync: bool = True) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._fsync = fsync
        self._seq = 0
        self._closed = False

        new_file = not self._path.exists() or self._path.stat().st_size == 0
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._path.open("a", encoding="utf-8")
        if new_file:
            header = {
                "kind": "header",
                "schema": LOG_SCHEMA_VERSION,
                "producer": "tangle",
            }
            self._fh.write(_stable_json(header) + "\n")
            self._flush()
        else:
            self._seq = _count_events(self._path)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def seq(self) -> int:
        return self._seq

    def append(self, event: Event) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("EventLogWriter is closed")
            payload = encode_event(event)
            payload_json = _stable_json(payload)
            record = {
                "kind": "event",
                "seq": self._seq,
                "hash": _line_hash(payload_json),
                "event": payload,
            }
            self._fh.write(_stable_json(record) + "\n")
            self._flush()
            self._seq += 1

    def _flush(self) -> None:
        self._fh.flush()
        if self._fsync:
            # Some filesystems (e.g. certain tmpfs) don't support fsync;
            # flush() above is enough for durability on those.
            with contextlib.suppress(OSError):
                os.fsync(self._fh.fileno())

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._fh.close()
            self._closed = True

    def __enter__(self) -> EventLogWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _count_events(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                break
            if rec.get("kind") == "event":
                count += 1
    return count


class EventLogReader:
    """Streams events back out of an append-only log, verifying integrity."""

    def __init__(self, path: str | os.PathLike[str], *, strict: bool = True) -> None:
        self._path = Path(path)
        self._strict = strict
        self._schema: int | None = None

    @property
    def schema(self) -> int | None:
        return self._schema

    def __iter__(self) -> Iterator[Event]:
        with self._path.open("r", encoding="utf-8") as fh:
            expected_seq = 0
            header_seen = False
            for lineno, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    if self._strict:
                        raise LogCorruptionError(f"invalid json at line {lineno}: {exc}") from exc
                    continue
                kind = rec.get("kind")
                if kind == "header":
                    header_seen = True
                    self._schema = int(rec.get("schema", 0))
                    if self._schema != LOG_SCHEMA_VERSION and self._strict:
                        raise LogCorruptionError(
                            f"unsupported schema {self._schema} (expected {LOG_SCHEMA_VERSION})"
                        )
                    continue
                if kind != "event":
                    if self._strict:
                        raise LogCorruptionError(f"unknown record kind {kind!r} at line {lineno}")
                    continue
                if self._strict and not header_seen:
                    raise LogCorruptionError("event record before header")
                if self._strict and rec.get("seq") != expected_seq:
                    raise LogCorruptionError(
                        f"seq gap at line {lineno}: got {rec.get('seq')} expected {expected_seq}"
                    )
                payload = rec.get("event")
                if not isinstance(payload, dict):
                    raise LogCorruptionError(f"missing event payload at line {lineno}")
                if self._strict:
                    want = rec.get("hash")
                    got = _line_hash(_stable_json(payload))
                    if want != got:
                        raise LogCorruptionError(
                            f"hash mismatch at line {lineno}: want {want} got {got}"
                        )
                expected_seq += 1
                yield decode_event(payload)
