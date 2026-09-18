"""Tests session_connections.py's registry: register/push/unregister,
and that pushing to an unregistered meeting_id is a safe no-op rather
than an error (a panel that's never been opened, or already closed,
must not break the backend push path)."""

import pytest

import session_connections


class _FakeWebSocket:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_json(self, message):
        if self.fail:
            raise ConnectionError("simulated send failure")
        self.sent.append(message)


@pytest.mark.asyncio
async def test_push_with_no_registered_connection_is_a_noop():
    await session_connections.push("no-such-meeting", {"type": "decision_batch"})


@pytest.mark.asyncio
async def test_register_then_push_delivers_message():
    ws = _FakeWebSocket()
    session_connections.register("meeting-a", ws)
    try:
        await session_connections.push("meeting-a", {"type": "decision_batch", "decisions": []})
        assert ws.sent == [{"type": "decision_batch", "decisions": []}]
    finally:
        session_connections.unregister("meeting-a", ws)


@pytest.mark.asyncio
async def test_unregister_stops_further_pushes():
    ws = _FakeWebSocket()
    session_connections.register("meeting-b", ws)
    session_connections.unregister("meeting-b", ws)

    await session_connections.push("meeting-b", {"type": "decision_batch"})
    assert ws.sent == []


@pytest.mark.asyncio
async def test_unregister_does_not_remove_a_newer_connection():
    # Simulates a reconnect: the old connection's cleanup must not
    # clobber a newer one already registered under the same meeting_id.
    old_ws = _FakeWebSocket()
    new_ws = _FakeWebSocket()
    session_connections.register("meeting-c", old_ws)
    session_connections.register("meeting-c", new_ws)

    session_connections.unregister("meeting-c", old_ws)

    await session_connections.push("meeting-c", {"type": "decision_batch"})
    assert new_ws.sent == [{"type": "decision_batch"}]

    session_connections.unregister("meeting-c", new_ws)


@pytest.mark.asyncio
async def test_push_failure_does_not_raise():
    ws = _FakeWebSocket(fail=True)
    session_connections.register("meeting-d", ws)
    try:
        await session_connections.push("meeting-d", {"type": "decision_batch"})
    finally:
        session_connections.unregister("meeting-d", ws)
