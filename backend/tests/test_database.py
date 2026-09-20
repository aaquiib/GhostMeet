"""
Tests PostgresDecisionStore directly against the isolated test DB
(tests/conftest.py, a real Postgres connection — see
test_postgres_connectivity.py for the standalone smoke test this
depends on): several concurrent create()/update_status() calls (the
same shape as real contention — decision creation racing the Slack
webhook's status updates) all commit correctly with no errors, and a
decision's full lifecycle — create, read back, update status, see the
update reflected — works end to end.
"""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from decision_detector import DecisionRecord
from decision_store import decision_store

MEETING_ID = "db-test-meeting"


def _make_record(text: str) -> DecisionRecord:
    return DecisionRecord(
        meeting_id=MEETING_ID,
        decision_text=text,
        speaker="spk_0",
        requires_action_from="Sarah",
        context="test context",
        confidence=0.9,
        urgency="medium",
        timestamp=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_concurrent_creates_and_updates_all_commit():
    records = [_make_record(f"concurrent decision {i}") for i in range(8)]

    # Fire several create() calls at once, then several update_status()
    # calls at once — this is exactly the shape of real contention
    # (decision creation vs. the Slack webhook's status updates)
    # Postgres handles natively via MVCC, no special setup needed.
    await asyncio.gather(*(decision_store.create(r) for r in records))

    await asyncio.gather(
        *(decision_store.update_status(r.id, "approved", f"U{i}") for i, r in enumerate(records))
    )

    recent = await decision_store.get_recent(MEETING_ID, limit=20)
    updated_ids = {r.id for r in records}
    matching = [r for r in recent if r.id in updated_ids]
    assert len(matching) == len(records)
    assert all(r.status == "approved" for r in matching)


@pytest.mark.asyncio
async def test_decision_lifecycle_create_read_update():
    record = _make_record("Whether to lifecycle-test this decision")

    await decision_store.create(record)

    recent = await decision_store.get_recent(MEETING_ID, limit=20)
    stored = next((r for r in recent if r.id == record.id), None)
    assert stored is not None
    assert stored.status == "pending"
    assert stored.decision_text == record.decision_text
    # The timestamp column is a plain (non-timezone) DateTime, which
    # strips tzinfo on the way back out regardless of backend — confirm
    # it's reattached rather than silently going naive.
    assert stored.timestamp.tzinfo is not None

    await decision_store.update_status(record.id, "approved", "U_APPROVER")

    recent_after = await decision_store.get_recent(MEETING_ID, limit=20)
    updated = next(r for r in recent_after if r.id == record.id)
    assert updated.status == "approved"


@pytest.mark.asyncio
async def test_update_status_on_unknown_id_does_not_raise():
    # Shouldn't happen in practice (the webhook only ever gets a
    # decision_id it itself put on a button), but must degrade
    # gracefully rather than crash the background task if it did.
    await decision_store.update_status(uuid.uuid4(), "approved", "U_X")
