"""
Shared pytest setup. Points the DB at an isolated file (set before any
test module imports database.py — conftest.py is collected first) so
tests never touch the real dev ghost.db, and ensures the schema exists
before any test runs: TestClient(app) without an explicit `with` block
never fires the FastAPI lifespan (which is what creates it under real
uvicorn), so tests create it directly instead.
"""

import os

os.environ.setdefault("GHOST_DATABASE_URL", "sqlite+aiosqlite:///test_ghost.db")

import asyncio
from pathlib import Path

import pytest

import database

_TEST_DB_PATH = Path(__file__).parent.parent / "test_ghost.db"


def _remove_test_db_files() -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{_TEST_DB_PATH}{suffix}").unlink(missing_ok=True)


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    _remove_test_db_files()
    asyncio.run(database.create_all())
    yield
    _remove_test_db_files()
