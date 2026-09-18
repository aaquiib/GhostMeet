"""
FastAPI app entrypoint. Importing `config` here first means a missing
required credential raises immediately on startup, not on first use.
"""

import asyncio
import logging
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from asr_base import MeetingLoggerAdapter
from config import settings  # noqa: F401  (import triggers validation)
from decision_detector import decision_pipeline
from demo_mode import run_demo_session
from transcribe_handler import get_asr_provider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ghost.main")

app = FastAPI(title="AI Meeting Ghost")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws/transcribe")
async def ws_transcribe(websocket: WebSocket, session_id: str | None = None):
    await websocket.accept()

    # A supplied session_id is trusted as-is: there's no server-side
    # session registry yet (that's Phase 5's job), so "recognized" here
    # just means "the client gave us one to reuse". Reusing it always
    # starts a NEW provider stream under that SAME meeting_session_id —
    # an AWS/Deepgram streaming connection can't literally be resumed
    # once dropped, only re-opened under the same logical session.
    meeting_session_id = session_id or str(uuid.uuid4())
    if not session_id:
        await websocket.send_json({"meeting_session_id": meeting_session_id})

    log = MeetingLoggerAdapter(logger, {"meeting_session_id": meeting_session_id})

    try:
        provider = await get_asr_provider(meeting_session_id, log)
    except Exception:
        log.exception("failed to start any ASR provider")
        await websocket.close(code=1011)
        return

    log.info("ASR provider started (%s)", type(provider).__name__)

    async def receive_loop() -> None:
        try:
            while True:
                data = await websocket.receive_bytes()
                await provider.send_audio(data)
        except WebSocketDisconnect:
            log.info("client disconnected")

    async def transcript_loop() -> None:
        async for event in provider.events():
            if event.is_partial:
                # Partial results are dropped in this phase — only
                # final results continue downstream.
                continue
            await websocket.send_json(event.model_dump(mode="json"))
            # process_transcript_event only ever blocks synchronously
            # on cheap buffering/regex work — the LLM call it may
            # trigger runs as an internally-managed background task,
            # so this never delays the transcript stream above.
            await decision_pipeline.process_transcript_event(event)

    receive_task = asyncio.create_task(receive_loop())
    transcript_task = asyncio.create_task(transcript_loop())

    try:
        # Client->provider and provider->client are independent,
        # simultaneous streams; run them concurrently so neither blocks
        # the other. asyncio.gather alone won't cancel a still-running
        # sibling when the other finishes first, so whichever side
        # finishes first (client disconnect, or the provider stream
        # ending/erroring) explicitly cancels the other rather than
        # leaving it waiting on a session nobody's using anymore.
        done, pending = await asyncio.wait(
            {receive_task, transcript_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
    except Exception:
        log.exception("error in transcribe session")
    finally:
        # Guaranteed even on exception or abrupt disconnect, so the
        # AWS/Deepgram connection never leaks.
        await provider.stop()
        log.info("ASR provider stopped")


@app.websocket("/ws/demo")
async def ws_demo(websocket: WebSocket):
    await run_demo_session(websocket)
