# src/tangle/detector/base.py

from typing import Protocol

from tangle.types import Cycle, Edge, LivelockPattern


class DeadlockDetector(Protocol):
    def on_edge_added(self, edge: Edge) -> Cycle | None: ...
    def full_scan(self) -> list[Cycle]: ...


class LivelockDetectorProtocol(Protocol):
    def on_message(
        self,
        from_agent: str,
        to_agent: str,
        body: bytes,
        workflow_id: str,
    ) -> LivelockPattern | None: ...
