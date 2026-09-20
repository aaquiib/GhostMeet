"""
Owns persistence for the raw meeting transcript, kept alongside
decision_store.py's decisions so the full transcript is stored durably
(Neon Postgres) rather than only relayed live over the WebSocket and
discarded the moment the connection closes. Every final TranscriptEvent
(partials are already dropped before this point — see main.py's
transcript_loop and demo_mode.py's send_script, the two call sites)
is persisted here.

Mirrors decision_store.py's shape deliberately: create() never raises
— a transcript-storage failure must not interrupt the live transcript
stream or decision detection, both of which already succeeded by the
time this is called — and get_recent() is what main.py's new
GET /transcript route uses to hydrate the side panel's chat feed on
open/reopen, the same role decision_store.get_recent() plays for
GET /decisions.

create() returns the id it generated so the caller (main.py/
demo_mode.py) can attach the same id to the live WebSocket frame —
without a shared id, a panel reopened mid-session could show a
duplicated tail where GET /transcript's hydration and the resumed live
stream briefly overlap, since (unlike TranscriptEvent, used elsewhere
for in-process detection) there was previously nothing for the client
to dedupe on. get_recent() returns plain dicts (not TranscriptEvent —
which has no id field, and every other consumer of that model doesn't
need one) for the same reason, mirroring how main.py's /search route
already builds plain dicts straight from ORM rows rather than
round-tripping through a Pydantic model.
"""

import logging
import uuid
from datetime import timezone

from sqlalchemy import select

import database
from transcription import TranscriptEvent

logger = logging.getLogger("ghost.transcript_store")


async def create(event: TranscriptEvent) -> "uuid.UUID | None":
    """Persists one final transcript line and returns its generated id
    (None on failure). Never raises — logged and swallowed, same fail-
    soft principle opensearch_client.py and decision_store.py's own
    persistence calls already follow: storage is additive, and a DB
    hiccup here must never interrupt a live meeting session."""
    line_id = uuid.uuid4()
    try:
        async with database.async_session_maker() as session:
            session.add(
                database.TranscriptLine(
                    id=line_id,
                    meeting_id=event.meeting_id,
                    speaker=event.speaker,
                    text=event.text,
                    confidence=event.confidence,
                    # asyncpg rejects a tz-aware datetime against this
                    # plain (non-timezone) column outright — same strip
                    # decision_store.PostgresDecisionStore.create()
                    # already makes, for the same reason.
                    timestamp=event.timestamp.replace(tzinfo=None),
                )
            )
            await session.commit()
        return line_id
    except Exception:
        logger.exception(
            "failed to persist transcript line for meeting_id=%s", event.meeting_id
        )
        return None


async def get_recent(meeting_id: str, limit: int = 500) -> list[dict]:
    """Oldest first (unlike decision_store.get_recent's newest-first —
    a transcript reads top-to-bottom, decisions are triaged newest-
    first), so the side panel's chat feed can render this list directly
    in speaking order on hydration without re-sorting. Shape matches
    the "transcript" WebSocket frame main.py/demo_mode.py send live
    (same keys, same id field) so the client's dedupe-by-id logic
    doesn't need two different shapes to handle."""
    async with database.async_session_maker() as session:
        result = await session.execute(
            select(database.TranscriptLine)
            .where(database.TranscriptLine.meeting_id == meeting_id)
            .order_by(database.TranscriptLine.timestamp.asc())
            .limit(limit)
        )
        rows = result.scalars().all()
    return [
        {
            "id": str(row.id),
            "meeting_id": row.meeting_id,
            "speaker": row.speaker,
            "text": row.text,
            "confidence": row.confidence,
            "timestamp": (
                row.timestamp if row.timestamp.tzinfo else row.timestamp.replace(tzinfo=timezone.utc)
            ).isoformat(),
            "is_partial": False,
        }
        for row in rows
    ]
