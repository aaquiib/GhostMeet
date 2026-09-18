"""
Owns the /ws/demo text-injection path: streams a pre-scripted list of
TranscriptEvent objects to the client, one every ~2 seconds, using the
same shared TranscriptEvent model the real ASR path produces. This is
the primary demo path (CLAUDE.md > Demo strategy) — it skips
ASRProvider entirely, so it works even if AWS Transcribe/Deepgram
setup is broken. The route itself is registered in main.py; this
module owns the session logic and the scripted data.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

from asr_base import MeetingLoggerAdapter, TranscriptEvent

logger = logging.getLogger("ghost.demo")

DEMO_MEETING_ID = "demo-meeting"
SEND_INTERVAL_SECONDS = 2

# Scripted lines live as their own JSON fixture, not inline in this
# module, so the demo script is easy to edit without touching session
# logic.
_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "demo_transcript.json"


def _load_script() -> list[dict]:
    with _FIXTURE_PATH.open() as f:
        return json.load(f)


async def run_demo_session(websocket: WebSocket) -> None:
    await websocket.accept()
    log = MeetingLoggerAdapter(logger, {"meeting_session_id": DEMO_MEETING_ID})
    log.info("demo session started")

    script = _load_script()

    try:
        for line in script:
            event = TranscriptEvent(
                text=line["text"],
                speaker=line["speaker"],
                timestamp=datetime.now(timezone.utc),
                confidence=line.get("confidence", 0.95),
                is_partial=False,
                meeting_id=DEMO_MEETING_ID,
            )
            await websocket.send_json(event.model_dump(mode="json"))
            log.info("sent demo event: %s: %r", event.speaker, event.text)
            await asyncio.sleep(SEND_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        log.info("client disconnected")
    finally:
        log.info("demo session ended")
