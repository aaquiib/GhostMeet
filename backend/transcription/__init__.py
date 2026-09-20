"""
Transcription boundary — everything that turns meeting audio into
TranscriptEvents, and nothing else.

Inside: the ASRProvider contract (asr_base.py), AWS Transcribe
Streaming as its sole real implementation (aws_transcribe.py), and the
scripted text-injection stand-in used for demos (demo_mode.py).

What crosses this boundary outward is TranscriptEvent — nothing in
here knows about decisions, Cedar, Slack, or the notification side, and
nothing provider-specific (an AWS `Result`, an `amazon_transcribe`
type) is allowed to leak past it.

Only the contract types are re-exported here. The providers themselves
(`aws_transcribe.get_asr_provider`, `demo_mode.run_demo_session`) are
imported directly from their modules by main.py, the composition root,
so that consuming a TranscriptEvent never drags the AWS SDK in with it.
"""

from transcription.asr_base import ASRProvider, MeetingLoggerAdapter, TranscriptEvent

__all__ = ["ASRProvider", "MeetingLoggerAdapter", "TranscriptEvent"]
