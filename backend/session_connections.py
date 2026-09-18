"""
Owns the registry mapping meeting_session_id -> the live WebSocket
connection for that session (on /ws/transcribe or /ws/demo), so code
that doesn't hold the WebSocket directly — notification_pipeline.py
(pushing decision_batch) and slack_webhook.py (pushing
decision_status_update) — can still push a message down it. main.py
registers/unregisters as each connection opens/closes; nothing else
should reach into this registry directly.

A meeting_id with no registered connection (panel never opened, or the
connection already ended) is not an error — the push is just dropped
and logged. GET /decisions is what backfills a panel that (re)opens
later; live pushes are a best-effort addition on top of that, not the
only way state reaches the panel.
"""

import logging

from fastapi import WebSocket

logger = logging.getLogger("ghost.session_connections")

_connections: dict[str, WebSocket] = {}


def register(meeting_id: str, websocket: WebSocket) -> None:
    _connections[meeting_id] = websocket


def unregister(meeting_id: str, websocket: WebSocket) -> None:
    # Only remove if it's still THIS connection — a reconnect may
    # already have registered a newer one under the same meeting_id by
    # the time this one's cleanup runs.
    if _connections.get(meeting_id) is websocket:
        del _connections[meeting_id]


async def push(meeting_id: str, message: dict) -> None:
    websocket = _connections.get(meeting_id)
    if websocket is None:
        logger.info(
            "no live connection for meeting_id=%s; dropping push of type=%s",
            meeting_id,
            message.get("type"),
        )
        return
    try:
        await websocket.send_json(message)
    except Exception:
        logger.exception("failed to push message to meeting_id=%s", meeting_id)
