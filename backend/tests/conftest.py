"""
Shared pytest setup. Points DATABASE_URL at an isolated Postgres
database (set before any test module imports config.py/database.py —
conftest.py is collected first) so tests never touch the real dev
`ghost` database, and ensures the schema exists before any test runs:
TestClient(app) without an explicit `with` block never fires the
FastAPI lifespan (which is what creates it under real uvicorn), so
tests create it directly instead.

Assumes a Postgres server is reachable at the same host/port as the
real dev DATABASE_URL, with a `ghost_test` database already created
(`createdb -U ghost -h localhost ghost_test`, or let
test_postgres_connectivity.py's failure point you at what's missing) —
this conftest creates the schema inside it, not the database itself.
"""

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://ghost:ghost@localhost:5432/ghost_test"
)

import asyncio

import pytest

import database


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    async def _reset_schema() -> None:
        # drop_all/create_all (not just create_all) so a schema change
        # between runs (e.g. widening the status enum) doesn't leave a
        # stale type/table behind from a previous test session.
        async with database.engine.begin() as conn:
            await conn.run_sync(database.Base.metadata.drop_all)
            await conn.run_sync(database.Base.metadata.create_all)

    asyncio.run(_reset_schema())
    yield

    async def _drop_schema() -> None:
        async with database.engine.begin() as conn:
            await conn.run_sync(database.Base.metadata.drop_all)

    asyncio.run(_drop_schema())
