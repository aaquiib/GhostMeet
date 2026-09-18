"""
Central app configuration. Loads and validates every credential/config
value later phases need from the environment (and .env in dev). Import
`settings` anywhere a value is needed — importing this module is what
makes a missing required credential fail loudly at startup instead of
surfacing as a confusing error deep in some phase-4 code path.
"""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # AWS — required. Transcribe Streaming is the primary ASR path.
    aws_region: str
    aws_access_key_id: str
    aws_secret_access_key: str

    # Slack — required. DMs are the only notification channel.
    slack_bot_token: str
    slack_signing_secret: str

    # LLM — required. Powers decision detection and drafted answers.
    llm_api_key: str

    # Deepgram — optional. Only used if AWS Transcribe setup breaks.
    deepgram_api_key: str | None = None

    # OpenSearch — optional. Not needed until phase 5 (P1); SQLite covers
    # decision history before that.
    opensearch_host: str | None = None
    opensearch_user: str | None = None
    opensearch_password: str | None = None

    # Detection tuning.
    confidence_threshold: float = Field(default=0.7)
    debounce_window_seconds: int = Field(default=12)

    # Audio format constants — reference these everywhere audio format
    # is specified (provider start calls, PCM decoding, etc.); never
    # repeat the literal values inline. Fixed by the extension's
    # capture pipeline (Phase 1), not meant to vary per deployment.
    SAMPLE_RATE_HZ: int = 16000
    AUDIO_ENCODING: str = "pcm"

    # Which ASR provider is primary. AWS Transcribe Streaming is the
    # default; Deepgram is the fallback (used directly if set to
    # "deepgram", or automatically if AWS fails to start). Swapping
    # providers is a config change, not a code change.
    asr_provider: Literal["aws", "deepgram"] = "aws"


settings = Settings()
