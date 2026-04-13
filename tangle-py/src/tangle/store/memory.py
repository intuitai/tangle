# src/tangle/store/memory.py

import threading

from tangle.types import Detection, DetectionType, Event


class MemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._detections: list[Detection] = []
        self._events: list[Event] = []
        self._closed = False

    def record_detection(self, detection: Detection) -> None:
        with self._lock:
            self._detections.append(detection)

    def record_event(self, event: Event) -> None:
        with self._lock:
            self._events.append(event)

    def list_detections(self, workflow_id: str, limit: int = 100) -> list[Detection]:
        with self._lock:
            results: list[Detection] = []
            for d in self._detections:
                wf = (
                    d.cycle.workflow_id
                    if d.cycle
                    else (d.livelock.workflow_id if d.livelock else "")
                )
                if wf == workflow_id:
                    results.append(d)
                if len(results) >= limit:
                    break
            return results

    def list_detections_by_type(self, dtype: DetectionType, limit: int = 100) -> list[Detection]:
        with self._lock:
            results: list[Detection] = []
            for d in self._detections:
                if d.type == dtype:
                    results.append(d)
                if len(results) >= limit:
                    break
            return results

    def get_workflow_events(self, workflow_id: str) -> list[Event]:
        with self._lock:
            return [e for e in self._events if e.workflow_id == workflow_id]

    def stats(self) -> dict[str, int]:
        with self._lock:
            deadlocks = sum(1 for d in self._detections if d.type == DetectionType.DEADLOCK)
            livelocks = sum(1 for d in self._detections if d.type == DetectionType.LIVELOCK)
            return {
                "total_detections": len(self._detections),
                "deadlocks_detected": deadlocks,
                "livelocks_detected": livelocks,
                "total_events": len(self._events),
            }

    def close(self) -> None:
        self._closed = True
