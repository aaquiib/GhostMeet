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
from collections import Counter
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from amazon_transcribe.auth import StaticCredentialResolver
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.model import Result as AwsResult

from transcription.asr_base import ASRProvider, MeetingLoggerAdapter, TranscriptEvent
from config import settings

logger = logging.getLogger("ghost.asr")


def _normalize_speaker_label(raw: "str | None") -> str:
    """Transcribe's *streaming* API labels speakers `"0"`, `"1"`, while
    its batch API — and the demo fixture, the side panel's
    speaker-override input, and this project's docs — all use
    `"spk_0"`/`"spk_1"`. Normalizing at the provider edge is what makes
    an override typed as `spk_0` actually match a live session's
    events; without it, overrides silently do nothing in real meetings
    while appearing to work in the demo."""
    if not raw:
        return "unknown"
    return f"spk_{raw}" if raw.isdigit() else raw


def _aws_result_to_event(result: AwsResult, meeting_id: str) -> TranscriptEvent:
    alternative = result.alternatives[0] if result.alternatives else None
    text = alternative.transcript if alternative else ""
    items = alternative.items if alternative else []

    # Transcribe's *streaming* diarization labels each item (word)
    # independently, not the result as a whole — a single result can
    # carry a stray mislabeled item (most often the very first word,
    # where the diarization model hasn't yet locked onto the speaker
    # cluster) while the rest of the words agree on the real speaker.
    # Taking only the first labeled item's speaker propagated that
    # jitter to the entire line, which is what produced both observed
    # symptoms: one continuous utterance getting split across two
    # different displayed speakers, and phantom extra speaker labels
    # showing up in a two-person conversation. Majority vote across all
    # labeled items is far more resistant to a single stray item.
    speaker_votes = Counter(item.speaker for item in items if item.speaker)
    speaker = _normalize_speaker_label(speaker_votes.most_common(1)[0][0] if speaker_votes else None)

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
