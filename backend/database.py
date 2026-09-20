"""
Owns the async SQLAlchemy engine/session setup and the Decision ORM
model backing PostgresDecisionStore (decision_store.py). Postgres
handles concurrent connections natively — decision creation (Phase 3)
and the Slack webhook's status updates (Phase 4) hitting the database
at the same time need no special journal-mode setup the way SQLite
did. create_all() is called from main.py's lifespan handler at
startup, not at import time — `async with engine.begin()` needs a
running event loop, which doesn't exist yet during a plain module
import.

No Alembic/migrations here — still hackathon scope, one schema created
once via create_all(). IMPORTANT: create_all() only creates tables/types
that don't exist yet; it does NOT alter an existing table's columns. If
the schema changes after this point (a new column, a widened enum,
etc.), the dev database needs to be dropped and recreated, not migrated
in place.
"""

import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from config import settings

# NullPool, not the default pooled engine: asyncpg's connections are
# bound to the event loop that created them, and a pooled connection
# reused from a different loop raises "attached to a different loop".
# That never came up under aiosqlite (SQLite), but does here whenever
# something creates its own short-lived loop against this same
# module-level engine — tests/conftest.py's session-scoped schema
# setup plus pytest-asyncio's function-scoped per-test loops being the
# concrete case. NullPool opens a fresh connection per checkout instead
# of reusing one across loops, which sidesteps the whole class of bug;
# the overhead is negligible at this project's scale.
engine = create_async_engine(settings.database_url, poolclass=NullPool)

async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# Native Postgres enum — covers exactly the status values in use
# elsewhere (decision_detector.py's DecisionRecord default, Cedar's
# denial status, slack_webhook.py's approve/reject, and Phase 6's
# answered_live). Writing anything outside this set is a database-level
# error at write time, not just an unenforced string.
DecisionStatus = ENUM(
    "pending",
    "approved",
    "rejected",
    "denied_by_policy",
    "answered_live",
    name="decision_status",
)


class Decision(Base):
    __tablename__ = "decisions"

    # Native Postgres UUID type — matches DecisionRecord.id's
    # uuid.UUID exactly, no string conversion needed at either end.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    meeting_id: Mapped[str] = mapped_column(index=True)
    decision_text: Mapped[str]
    speaker: Mapped[str]
    requires_action_from: Mapped[str | None]
    context: Mapped[str]
    confidence: Mapped[float]
    urgency: Mapped[str]
    timestamp: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(DecisionStatus, default="pending")
    approved_by: Mapped[str | None] = mapped_column(default=None)


async def create_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> "AsyncSession":
    """FastAPI dependency — `Depends(get_session)` — for routes that
    need direct DB access (e.g. main.py's /search SQLite/Postgres
    LIKE-query fallback). Code that isn't a route (PostgresDecisionStore)
    opens its own session via async_session_maker() directly; Depends
    only resolves inside FastAPI's request handling."""
    async with async_session_maker() as session:
        yield session
