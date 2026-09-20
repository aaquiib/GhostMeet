"""
Shared ASR provider contract: the abstract interface a provider
implements (currently just AWSTranscribeProvider — see
aws_transcribe.py), and the TranscriptEvent shape it must produce.
Kept as its own interface (not collapsed into aws_transcribe.py)
so a provider is still mockable for tests even with only one real
implementation. Nothing provider-specific belongs here, and nothing
provider-specific is allowed to leak past this boundary into the rest
of the app.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import AsyncIterator

from pydantic import BaseModel


class MeetingLoggerAdapter(logging.LoggerAdapter):
    """Prepends meeting_session_id to every log line so one meeting's
    behavior can be traced independently of others during concurrent
    testing. Prepending to the message text (rather than relying on a
    %(meeting_session_id)s field in a shared formatter) means it can't
    break formatting for any other logger in the process — uvicorn's,
    the AWS Transcribe SDK's, or anything else — that doesn't carry
    this field."""

    def process(self, msg, kwargs):
        return f"[meeting_session_id={self.extra['meeting_session_id']}] {msg}", kwargs


class TranscriptEvent(BaseModel):
    text: str
    speaker: str
    timestamp: datetime
    confidence: float
    is_partial: bool
    meeting_id: str


class ASRProvider(ABC):
    """One streaming ASR session: start it, feed it audio, read back
    transcript events, stop it. Implementations own their provider
    SDK's connection lifecycle and translate its output into
    TranscriptEvent — callers never see provider-specific types."""

    @abstractmethod
    async def start(self, meeting_session_id: str) -> None:
        """Open the provider's streaming connection for this meeting."""

    @abstractmethod
    async def send_audio(self, chunk: bytes) -> None:
        """Write one PCM chunk to the open stream."""

    @abstractmethod
    def events(self) -> AsyncIterator[TranscriptEvent]:
        """Async-iterate TranscriptEvents as the provider produces them."""

    @abstractmethod
    async def stop(self) -> None:
        """Close the provider connection cleanly."""
