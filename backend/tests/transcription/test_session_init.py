"""
Tests the session_init handshake (Part A) on /ws/demo (no ASR provider
needed, so no real AWS/Deepgram credentials required) and confirms the
same rejection behavior on /ws/transcribe: identity is correctly
stored and used for Tier-1 matching, a missing/malformed session_init
is rejected loudly rather than silently defaulted, and a mid-session
speaker_override takes effect.
"""

import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import main
from decision_detector import decision_pipeline
from transcription.demo_mode import DEMO_MEETING_ID

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def isolate_demo_session():
    """The demo endpoint always uses the same fixed meeting id, so
    without this, state (buffer, speaker map, ...) would leak between
    tests in this file — session_init itself (watch names / slack
    target) is reset on every connect, but the rest of the per-session
    state isn't."""
    decision_pipeline._sessions.pop(DEMO_MEETING_ID, None)
    yield
    decision_pipeline._sessions.pop(DEMO_MEETING_ID, None)


def test_missing_session_init_on_demo_is_rejected():
    with client.websocket_connect("/ws/demo") as ws:
        ws.send_json({"type": "not_session_init"})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert "session_init" in error["message"]

        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
        assert exc_info.value.code == 1008


def test_incomplete_session_init_on_demo_is_rejected():
    with client.websocket_connect("/ws/demo") as ws:
        # type is right, but required fields are missing — must still
        # be rejected, not silently defaulted to a hardcoded name.
        ws.send_json({"type": "session_init", "watched_user_name_variants": []})
        error = ws.receive_json()
        assert error["type"] == "error"

        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_missing_session_init_on_transcribe_is_rejected():
    with client.websocket_connect("/ws/transcribe") as ws:
        session_msg = ws.receive_json()
        assert "meeting_session_id" in session_msg

        # Sends audio-shaped bytes instead of session_init — rejection
        # happens before any ASR provider is started, so this needs no
        # real AWS/Deepgram credentials.
        ws.send_bytes(b"\x00\x00\x00\x00")
        error = ws.receive_json()
        assert error["type"] == "error"

        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
        assert exc_info.value.code == 1008


def test_session_init_stores_identity_for_tier1():
    with client.websocket_connect("/ws/demo") as ws:
        ws.send_json(
            {
                "type": "session_init",
                "watched_user_name_variants": ["Zara", "Zara Q"],
                "slack_target": "zara@example.com",
            }
        )
        first_event = ws.receive_json()
        assert first_event["meeting_id"] == DEMO_MEETING_ID

    assert decision_pipeline.get_slack_target(DEMO_MEETING_ID) == "zara@example.com"

    state = decision_pipeline._sessions[DEMO_MEETING_ID]
    assert state.watch_names_pattern is not None
    assert state.watch_names_pattern.search("Hey Zara, question for you")
    assert state.watch_names_pattern.search("Zara Q, are you around?")
    assert not state.watch_names_pattern.search("no name mentioned here")


def test_speaker_override_takes_effect_mid_session():
    with client.websocket_connect("/ws/demo") as ws:
        ws.send_json(
            {
                "type": "session_init",
                # A name that won't appear in the scripted transcript,
                # so this test doesn't also trigger real Tier-2/LLM
                # calls as a side effect.
                "watched_user_name_variants": ["Nobody Matching"],
                "slack_target": "nobody@example.com",
            }
        )
        ws.receive_json()  # first scripted event

        ws.send_json({"type": "speaker_override", "label": "spk_9", "name": "Priya"})
        # Give the concurrent control-message loop a moment to process
        # it — TestClient runs the real ASGI app on its own thread, so
        # this is real (short) wall-clock time, not a mocked clock.
        time.sleep(0.3)

    state = decision_pipeline._sessions[DEMO_MEETING_ID]
    assert state.speaker_mapper.resolve("spk_9") == "Priya"
