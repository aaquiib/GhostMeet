"""
Tests POST /slack/interaction: signature verification against the raw
body, and that a button click's decision_id/status reach
decision_store.update_status via the BackgroundTask after the response
is already sent.

Exercises the current single "✓ Read" button only (action_id
"decision_read", value "{id}:read") — the three-button Approve/Reject/
Join layout this file previously tested is long gone (see CLAUDE.md's
mention-type migration note); those cases are replaced below with a
single "unrecognized action_id is ignored" test.
"""

import time
import urllib.parse
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import main
import session_connections
from notifications import slack_webhook
from decision_detector import DecisionRecord

client = TestClient(main.app)


def _signed_headers(body: bytes, timestamp: str | None = None) -> dict:
    timestamp = timestamp or str(int(time.time()))
    signature = slack_webhook._verifier.generate_signature(timestamp=timestamp, body=body)
    return {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": signature,
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _interaction_body(action_id: str, value: str, user_id: str = "U123") -> bytes:
    payload = {
        "actions": [{"action_id": action_id, "value": value}],
        "user": {"id": user_id},
    }
    import json

    return urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()


def test_invalid_signature_is_rejected():
    body = _interaction_body("decision_read", f"{uuid.uuid4()}:read")
    headers = _signed_headers(body)
    headers["X-Slack-Signature"] = "v0=deadbeef"  # wrong signature

    response = client.post("/slack/interaction", content=body, headers=headers)
    assert response.status_code == 401


def test_read_button_updates_decision_status(monkeypatch):
    decision_id = uuid.uuid4()
    calls = []

    async def fake_update_status(decision_id_arg, status, approved_by):
        calls.append((decision_id_arg, status, approved_by))

    monkeypatch.setattr(slack_webhook.decision_store, "update_status", fake_update_status)

    body = _interaction_body("decision_read", f"{decision_id}:read", user_id="U_READER")
    headers = _signed_headers(body)

    response = client.post("/slack/interaction", content=body, headers=headers)
    assert response.status_code == 200

    # update_status runs via BackgroundTasks *after* the response is
    # sent — TestClient runs background tasks synchronously as part of
    # completing the request, so it's already done by the time we get
    # here, but poll briefly for robustness against that changing.
    for _ in range(20):
        if calls:
            break
        time.sleep(0.05)

    # "read" is a lightweight acknowledgment, not an approval claim —
    # it reuses the "approved" status value rather than a new enum
    # member (see slack_webhook.py), so that's what's actually stored.
    assert calls == [(decision_id, "approved", "U_READER")]


def test_read_button_pushes_decision_status_update_to_panel(monkeypatch):
    decision_id = uuid.uuid4()
    updated_record = DecisionRecord(
        id=decision_id,
        meeting_id="panel-push-meeting",
        decision_text="Whether to test the push",
        speaker="spk_0",
        requires_action_from="Sarah",
        context="ctx",
        confidence=0.9,
        urgency="medium",
        timestamp=datetime.now(timezone.utc),
        status="approved",
    )

    async def fake_update_status(decision_id_arg, status, approved_by):
        return updated_record

    monkeypatch.setattr(slack_webhook.decision_store, "update_status", fake_update_status)

    pushes = []

    async def fake_push(meeting_id, message):
        pushes.append((meeting_id, message))

    monkeypatch.setattr(session_connections, "push", fake_push)

    body = _interaction_body("decision_read", f"{decision_id}:read", user_id="U_READER")
    headers = _signed_headers(body)

    response = client.post("/slack/interaction", content=body, headers=headers)
    assert response.status_code == 200

    for _ in range(20):
        if pushes:
            break
        time.sleep(0.05)

    assert pushes == [
        (
            "panel-push-meeting",
            {
                "type": "decision_status_update",
                "decision_id": str(decision_id),
                "status": "approved",
                "approved_by": "U_READER",
            },
        )
    ]


def test_unknown_decision_id_does_not_push(monkeypatch):
    async def fake_update_status(decision_id_arg, status, approved_by):
        return None  # unknown decision_id

    monkeypatch.setattr(slack_webhook.decision_store, "update_status", fake_update_status)

    pushes = []

    async def fake_push(meeting_id, message):
        pushes.append((meeting_id, message))

    monkeypatch.setattr(session_connections, "push", fake_push)

    body = _interaction_body("decision_read", f"{uuid.uuid4()}:read")
    headers = _signed_headers(body)

    response = client.post("/slack/interaction", content=body, headers=headers)
    assert response.status_code == 200
    time.sleep(0.2)
    assert pushes == []


def test_unrecognized_action_id_is_a_noop(monkeypatch):
    """Covers any action_id this endpoint doesn't recognize — an old
    client still sending the retired three-button layout's
    decision_approve/decision_reject, an unrelated join_meeting action,
    or anything else. Must never update the store on an unrecognized
    action_id."""
    calls = []

    async def fake_update_status(decision_id_arg, status, approved_by):
        calls.append((decision_id_arg, status, approved_by))

    monkeypatch.setattr(slack_webhook.decision_store, "update_status", fake_update_status)

    body = _interaction_body("decision_approve", f"{uuid.uuid4()}:approve")
    headers = _signed_headers(body)

    response = client.post("/slack/interaction", content=body, headers=headers)
    assert response.status_code == 200
    assert calls == []


def test_unrecognized_status_word_is_a_noop(monkeypatch):
    """A recognized action_id but an unexpected status word in the
    value (e.g. a stale client still sending ":done") must also not
    update the store."""
    calls = []

    async def fake_update_status(decision_id_arg, status, approved_by):
        calls.append((decision_id_arg, status, approved_by))

    monkeypatch.setattr(slack_webhook.decision_store, "update_status", fake_update_status)

    body = _interaction_body("decision_read", f"{uuid.uuid4()}:done")
    headers = _signed_headers(body)

    response = client.post("/slack/interaction", content=body, headers=headers)
    assert response.status_code == 200
    assert calls == []
