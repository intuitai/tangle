# src/tangle/server/idempotency.py

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any


class IdempotencyCache:
    """Bounded LRU cache of Idempotency-Key -> cached response body.

    The cache lives on the FastAPI app state and is process-local. It is
    intended to absorb at-least-once retries from clients within a short
    window; durable cross-instance deduplication should be layered on top
    (e.g. at a load balancer or via a shared store).
    """

    def __init__(self, max_size: int) -> None:
        self._max = max_size
        self._data: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._max > 0

    def get(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return dict(self._data[key])

    def put(self, key: str, response: dict[str, Any]) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._data[key] = dict(response)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)
