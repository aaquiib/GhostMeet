"""
Owns the async SQLAlchemy engine/session setup and the Decision ORM
model backing SQLiteDecisionStore (decision_store.py). WAL journal mode
is set on every new DBAPI connection — required, not optional, once
decision creation (Phase 3) and the Slack webhook's status updates
(Phase 4) can hit the same file concurrently; SQLite's default
rollback-journal mode serializes writers hard enough to raise
"database is locked" under that. create_all() is called from main.py's
lifespan handler at startup, not at import time — `async with
engine.begin()` needs a running event loop, which doesn't exist yet
during a plain module import.
"""

import os
from datetime import datetime

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Overridable so tests can point at an isolated file instead of the
# real dev DB (see tests/conftest.py, which sets this before any test
# module imports this one).
DATABASE_URL = os.environ.get("GHOST_DATABASE_URL", "sqlite+aiosqlite:///ghost.db")

engine = create_async_engine(DATABASE_URL)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Decision(Base):
    __tablename__ = "decisions"

    # String, not a native UUID column type — matches
    # DecisionRecord.id's str(uuid.UUID) form exactly, and SQLite has
    # no native UUID type anyway.
    id: Mapped[str] = mapped_column(primary_key=True)
    meeting_id: Mapped[str] = mapped_column(index=True)
    decision_text: Mapped[str]
    speaker: Mapped[str]
    requires_action_from: Mapped[str | None]
    context: Mapped[str]
    confidence: Mapped[float]
    urgency: Mapped[str]
    timestamp: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(default="pending")
    approved_by: Mapped[str | None] = mapped_column(default=None)


async def create_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> "AsyncSession":
    """FastAPI dependency — `Depends(get_session)` — for routes that
    need direct DB access (e.g. main.py's /search SQLite fallback).
    Code that isn't a route (SQLiteDecisionStore) opens its own session
    via async_session_maker() directly; Depends only resolves inside
    FastAPI's request handling."""
    async with async_session_maker() as session:
        yield session
