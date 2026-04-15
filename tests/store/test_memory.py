# tests/store/test_memory.py

from tangle.store.memory import MemoryStore
from tests.store.conformance import run_store_conformance


def test_memory_store_conformance() -> None:
    run_store_conformance(MemoryStore)
