"""
Owns the streaming ASR connection: AWSTranscribeProvider, the sole
ASRProvider implementation (asr_base.py) — AWS Transcribe Streaming is
the only supported ASR path, no fallback provider. get_asr_provider()
is the single entry point main.py uses to start it.

Everything AWS-specific in this project lives behind this module: the
`amazon_transcribe` SDK is imported here and nowhere else, and its
types are translated into TranscriptEvent before anything leaves.
"""

import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from amazon_transcribe.auth import StaticCredentialResolver
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.model import Result as AwsResult

from transcription.asr_base import ASRProvider, MeetingLoggerAdapter, TranscriptEvent
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
    """The sole ASR provider. Uses show_speaker_label for diarization on
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


async def get_asr_provider(
    meeting_session_id: str, log: "MeetingLoggerAdapter | logging.Logger" = logger
) -> ASRProvider:
    """Starts and returns AWSTranscribeProvider — the sole ASR path, no
    fallback. If AWS Transcribe fails to start (bad credentials,
    unreachable region, etc.), this raises and the caller
    (main.py's ws_transcribe) is responsible for closing the socket."""
    provider = AWSTranscribeProvider()
    await provider.start(meeting_session_id)
    return provider
