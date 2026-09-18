"""
Owns the streaming ASR connection: AWSTranscribeProvider (primary) and
DeepgramProvider (used when ASR_PROVIDER=deepgram, or automatically as
a fallback if AWS fails to start), both implementing the ASRProvider
interface from asr_base.py. get_asr_provider() is the single entry
point main.py uses to start the right one.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from amazon_transcribe.auth import StaticCredentialResolver
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.model import Result as AwsResult
from deepgram import (
    DeepgramClient,
    LiveOptions,
    LiveResultResponse,
    LiveTranscriptionEvents,
)

from asr_base import ASRProvider, MeetingLoggerAdapter, TranscriptEvent
from config import settings

logger = logging.getLogger("ghost.asr")


def _aws_result_to_event(result: AwsResult, meeting_id: str) -> TranscriptEvent:
    alternative = result.alternatives[0] if result.alternatives else None
    text = alternative.transcript if alternative else ""
    items = alternative.items if alternative else []

    # One result is one speaker turn under Transcribe's diarization, so
    # the first labeled item represents the whole result.
    speaker = next((item.speaker for item in items if item.speaker), "unknown")

    confidences = [
        item.confidence
        for item in items
        if item.item_type == "pronunciation" and item.confidence is not None
    ]
    confidence = sum(confidences) / len(confidences) if confidences else 0.0

    return TranscriptEvent(
        text=text,
        speaker=speaker,
        timestamp=datetime.now(timezone.utc),
        confidence=confidence,
        is_partial=bool(result.is_partial),
        meeting_id=meeting_id,
    )


class AWSTranscribeProvider(ASRProvider):
    """Primary ASR provider. Uses show_speaker_label for diarization on
    the single mixed-channel tab-audio stream — NOT
    enable_channel_identification/number_of_channels, which are for
    true multi-channel audio (one speaker per channel), which tab audio
    isn't. See CLAUDE.md > Scope > Diarization."""

    def __init__(self) -> None:
        self._client = TranscribeStreamingClient(
            region=settings.aws_region,
            credential_resolver=StaticCredentialResolver(
                access_key_id=settings.aws_access_key_id,
                secret_access_key=settings.aws_secret_access_key,
            ),
        )
        self._stream = None
        self._meeting_id: Optional[str] = None

    async def start(self, meeting_session_id: str) -> None:
        self._meeting_id = meeting_session_id
        self._stream = await self._client.start_stream_transcription(
            language_code="en-US",
            media_sample_rate_hz=settings.SAMPLE_RATE_HZ,
            media_encoding=settings.AUDIO_ENCODING,
            show_speaker_label=True,
        )

    async def send_audio(self, chunk: bytes) -> None:
        await self._stream.input_stream.send_audio_event(audio_chunk=chunk)

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        async for aws_event in self._stream.output_stream:
            for result in aws_event.transcript.results:
                yield _aws_result_to_event(result, self._meeting_id)

    async def stop(self) -> None:
        if self._stream is not None:
            await self._stream.input_stream.end_stream()


def _deepgram_result_to_event(result: LiveResultResponse, meeting_id: str) -> TranscriptEvent:
    alternative = result.channel.alternatives[0] if result.channel.alternatives else None
    text = alternative.transcript if alternative else ""
    words = alternative.words if alternative else []

    # Deepgram labels speakers by integer index; format to match AWS's
    # "spk_N" convention so TranscriptEvent.speaker is provider-agnostic.
    speaker_index = next((w.speaker for w in words if w.speaker is not None), None)
    speaker = f"spk_{speaker_index}" if speaker_index is not None else "unknown"

    confidence = alternative.confidence if alternative else 0.0

    return TranscriptEvent(
        text=text,
        speaker=speaker,
        timestamp=datetime.now(timezone.utc),
        confidence=confidence,
        is_partial=not result.is_final,
        meeting_id=meeting_id,
    )


class DeepgramProvider(ASRProvider):
    """Fallback ASR provider — same downstream shape as AWS, just a
    different vendor. diarize=True gives speaker diarization on the
    same single mixed-channel mono stream; no multichannel needed."""

    # AWS Transcribe's media_encoding vocabulary (settings.AUDIO_ENCODING
    # == "pcm") isn't the string Deepgram expects for raw 16-bit PCM —
    # this is the one place that translation has to live.
    _DEEPGRAM_ENCODING = "linear16"

    def __init__(self) -> None:
        self._client = DeepgramClient(settings.deepgram_api_key or "")
        self._connection = None
        self._queue: "asyncio.Queue[TranscriptEvent]" = asyncio.Queue()
        self._meeting_id: Optional[str] = None

    async def start(self, meeting_session_id: str) -> None:
        self._meeting_id = meeting_session_id
        self._connection = self._client.listen.asyncwebsocket.v("1")

        async def on_transcript(_client, result: LiveResultResponse = None, **_kwargs):
            if result is None:
                return
            await self._queue.put(_deepgram_result_to_event(result, meeting_session_id))

        self._connection.on(LiveTranscriptionEvents.Transcript, on_transcript)

        options = LiveOptions(
            encoding=self._DEEPGRAM_ENCODING,
            sample_rate=settings.SAMPLE_RATE_HZ,
            channels=1,
            diarize=True,
            interim_results=True,
        )
        started = await self._connection.start(options)
        if not started:
            raise RuntimeError("Deepgram connection failed to start")

    async def send_audio(self, chunk: bytes) -> None:
        await self._connection.send(chunk)

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            yield await self._queue.get()

    async def stop(self) -> None:
        if self._connection is not None:
            await self._connection.finish()


async def get_asr_provider(
    meeting_session_id: str, log: "MeetingLoggerAdapter | logging.Logger" = logger
) -> ASRProvider:
    """Starts and returns the configured ASR provider. ASR_PROVIDER=aws
    (the default) falls back to Deepgram automatically if AWS Transcribe
    fails to start; ASR_PROVIDER=deepgram uses Deepgram directly."""

    if settings.asr_provider == "deepgram":
        provider = DeepgramProvider()
        await provider.start(meeting_session_id)
        return provider

    provider = AWSTranscribeProvider()
    try:
        await provider.start(meeting_session_id)
        return provider
    except Exception:
        log.warning("AWS Transcribe failed to start; falling back to Deepgram", exc_info=True)
        fallback = DeepgramProvider()
        await fallback.start(meeting_session_id)
        return fallback
