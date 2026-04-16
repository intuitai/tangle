# tests/replay/test_log.py

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tangle.replay.log import (
    LOG_SCHEMA_VERSION,
    EventLogReader,
    EventLogWriter,
    LogCorruptionError,
    decode_event,
    encode_event,
)
from tangle.types import Event, EventType

if TYPE_CHECKING:
    from pathlib import Path


def _ev(
    type: EventType = EventType.WAIT_FOR,
    timestamp: float = 1.0,
    workflow_id: str = "wf",
    from_agent: str = "A",
    to_agent: str = "B",
    resource: str = "",
    message_body: bytes = b"",
    metadata: dict[str, str] | None = None,
) -> Event:
    return Event(
        type=type,
        timestamp=timestamp,
        workflow_id=workflow_id,
        from_agent=from_agent,
        to_agent=to_agent,
        resource=resource,
        message_body=message_body,
        metadata=metadata or {},
    )


def test_event_roundtrip_preserves_every_field() -> None:
    ev = _ev(
        type=EventType.SEND,
        message_body=b"\x00\x01\xffbinary",
        metadata={"k": "v", "x": "y"},
    )
    assert decode_event(encode_event(ev)) == ev


def test_writer_creates_header_and_appends_events(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    with EventLogWriter(log, fsync=False) as w:
        w.append(_ev(from_agent="A", to_agent="B", timestamp=1.0))
        w.append(_ev(from_agent="B", to_agent="C", timestamp=2.0))
        assert w.seq == 2

    events = list(EventLogReader(log))
    assert len(events) == 2
    assert events[0].from_agent == "A" and events[0].to_agent == "B"
    assert events[1].from_agent == "B" and events[1].to_agent == "C"


def test_reader_exposes_schema_version(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    with EventLogWriter(log, fsync=False) as w:
        w.append(_ev())
    reader = EventLogReader(log)
    list(reader)  # drain to populate schema
    assert reader.schema == LOG_SCHEMA_VERSION


def test_reopening_writer_continues_sequence(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    with EventLogWriter(log, fsync=False) as w:
        w.append(_ev(timestamp=1.0))
        w.append(_ev(timestamp=2.0))
    with EventLogWriter(log, fsync=False) as w:
        assert w.seq == 2
        w.append(_ev(timestamp=3.0))
        assert w.seq == 3

    events = list(EventLogReader(log))
    assert [e.timestamp for e in events] == [1.0, 2.0, 3.0]


def test_corrupted_line_is_detected(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    with EventLogWriter(log, fsync=False) as w:
        w.append(_ev())

    raw = log.read_text().splitlines()
    # Flip a character inside the event payload so the hash no longer matches.
    tampered = raw[-1].replace('"from_agent":"A"', '"from_agent":"Z"')
    log.write_text("\n".join([*raw[:-1], tampered]) + "\n")

    with pytest.raises(LogCorruptionError):
        list(EventLogReader(log))


def test_truncated_tail_tolerated_in_non_strict_mode(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    with EventLogWriter(log, fsync=False) as w:
        w.append(_ev(timestamp=1.0))
        w.append(_ev(timestamp=2.0))

    raw = log.read_text()
    # Chop last line in half — simulates a crash mid-write.
    half = raw[: len(raw) - 10]
    log.write_text(half)

    events = list(EventLogReader(log, strict=False))
    assert len(events) == 1
    assert events[0].timestamp == 1.0


def test_schema_mismatch_raises(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text('{"kind":"header","schema":999}\n')
    with pytest.raises(LogCorruptionError):
        list(EventLogReader(log))
