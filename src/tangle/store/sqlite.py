# src/tangle/store/sqlite.py

import json
import sqlite3
import threading

from tangle.types import (
    Cycle,
    Detection,
    DetectionType,
    Event,
    EventType,
    LivelockPattern,
    Severity,
)


class SQLiteStore:
    def __init__(self, path: str = "tangle.db") -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS detections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                severity TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at REAL NOT NULL DEFAULT (julianday('now'))
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                from_agent TEXT NOT NULL,
                to_agent TEXT NOT NULL DEFAULT '',
                resource TEXT NOT NULL DEFAULT '',
                timestamp REAL NOT NULL,
                data TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_detections_workflow ON detections(workflow_id);
            CREATE INDEX IF NOT EXISTS idx_detections_type ON detections(type);
            CREATE INDEX IF NOT EXISTS idx_events_workflow ON events(workflow_id);
        """)

    def record_detection(self, detection: Detection) -> None:
        wf = (
            detection.cycle.workflow_id
            if detection.cycle
            else (detection.livelock.workflow_id if detection.livelock else "")
        )
        data: dict[str, object] = {
            "type": detection.type.value,
            "severity": detection.severity.value,
        }
        if detection.cycle:
            data["cycle"] = {
                "id": detection.cycle.id,
                "agents": detection.cycle.agents,
                "workflow_id": detection.cycle.workflow_id,
                "resolved": detection.cycle.resolved,
            }
        if detection.livelock:
            data["livelock"] = {
                "id": detection.livelock.id,
                "agents": detection.livelock.agents,
                "pattern_length": detection.livelock.pattern_length,
                "repeat_count": detection.livelock.repeat_count,
                "workflow_id": detection.livelock.workflow_id,
                "resolved": detection.livelock.resolved,
            }
        with self._lock:
            self._conn.execute(
                "INSERT INTO detections (type, severity, workflow_id, data) VALUES (?, ?, ?, ?)",
                (detection.type.value, detection.severity.value, wf, json.dumps(data)),
            )
            self._conn.commit()

    def record_event(self, event: Event) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events"
                " (type, workflow_id, from_agent, to_agent, resource, timestamp)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.type.value,
                    event.workflow_id,
                    event.from_agent,
                    event.to_agent,
                    event.resource,
                    event.timestamp,
                ),
            )
            self._conn.commit()

    def list_detections(self, workflow_id: str, limit: int = 100) -> list[Detection]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT type, severity, data FROM detections WHERE workflow_id = ? LIMIT ?",
                (workflow_id, limit),
            )
            return [self._row_to_detection(row) for row in cursor.fetchall()]

    def list_detections_by_type(self, dtype: DetectionType, limit: int = 100) -> list[Detection]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT type, severity, data FROM detections WHERE type = ? LIMIT ?",
                (dtype.value, limit),
            )
            return [self._row_to_detection(row) for row in cursor.fetchall()]

    def get_workflow_events(self, workflow_id: str) -> list[Event]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT type, workflow_id, from_agent, to_agent,"
                " resource, timestamp FROM events WHERE workflow_id = ?",
                (workflow_id,),
            )
            return [
                Event(
                    type=EventType(row[0]),
                    workflow_id=row[1],
                    from_agent=row[2],
                    to_agent=row[3],
                    resource=row[4],
                    timestamp=row[5],
                )
                for row in cursor.fetchall()
            ]

    def stats(self) -> dict[str, int]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
            deadlocks = self._conn.execute(
                "SELECT COUNT(*) FROM detections WHERE type = ?", ("deadlock",)
            ).fetchone()[0]
            livelocks = self._conn.execute(
                "SELECT COUNT(*) FROM detections WHERE type = ?", ("livelock",)
            ).fetchone()[0]
            events = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            return {
                "total_detections": total,
                "deadlocks_detected": deadlocks,
                "livelocks_detected": livelocks,
                "total_events": events,
            }

    def close(self) -> None:
        import contextlib

        with self._lock, contextlib.suppress(Exception):
            self._conn.close()

    def _row_to_detection(self, row: tuple[str, str, str]) -> Detection:
        data = json.loads(row[2])
        dtype = DetectionType(row[0])
        severity = Severity(row[1])
        cycle = None
        livelock = None
        if "cycle" in data:
            c = data["cycle"]
            cycle = Cycle(
                id=c["id"],
                agents=c["agents"],
                workflow_id=c["workflow_id"],
                resolved=c.get("resolved", False),
            )
        if "livelock" in data:
            ll = data["livelock"]
            livelock = LivelockPattern(
                id=ll["id"],
                agents=ll["agents"],
                pattern_length=ll["pattern_length"],
                repeat_count=ll["repeat_count"],
                workflow_id=ll["workflow_id"],
                resolved=ll.get("resolved", False),
            )
        return Detection(type=dtype, severity=severity, cycle=cycle, livelock=livelock)
