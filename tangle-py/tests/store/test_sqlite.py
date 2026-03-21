# tests/store/test_sqlite.py

import itertools
from pathlib import Path

from tangle.store.sqlite import SQLiteStore
from tests.store.conformance import run_store_conformance


def test_sqlite_store_conformance(tmp_path: Path) -> None:
    counter = itertools.count()

    def factory() -> SQLiteStore:
        db_path = str(tmp_path / f"test_{next(counter)}.db")
        return SQLiteStore(db_path)

    run_store_conformance(factory)
