# src/tangle/resolver/base.py

from typing import Protocol

from tangle.types import Detection


class Resolver(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def is_notification(self) -> bool: ...

    def resolve(self, detection: Detection) -> None: ...
