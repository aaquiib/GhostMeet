"""
Throwaway WAV-file test harness for /ws/transcribe — reads a short WAV
file, converts it to 16kHz mono 16-bit PCM if it isn't already, and
streams it through the backend exactly as the extension would (same
4096-sample binary frames, paced to real time), printing every
transcript event received. Fast, repeatable way to test ASR changes
without a live Google Meet call.

Defaults to scripts/sample_audio/two_speakers_sample.wav — a synthetic
placeholder (two alternating tones simulating turn-taking) since no
real recording is available in this sandbox. Point it at a real ~20s
two-person recording (`python3 feed_wav_file.py path/to/real.wav`) for
an actual ASR/diarization check; the synthetic file only proves the
streaming plumbing works, not transcription quality.

Usage:
    python3 scripts/feed_wav_file.py [wav_path] [--url ws://host:port/ws/transcribe]
        [--watch-name NAME ...] [--slack-target you@example.com]

Sends the mandatory session_init handshake (Phase 4) before any audio
— the backend rejects a connection that sends anything else first.
--watch-name can be repeated for multiple name variants; defaults to
a name that won't match anything in real speech, since the point of
this script is exercising ASR, not decision detection (pass your own
name to also exercise that).

Requires `websockets` (already in backend/requirements.txt — run from
backend/.venv). Uses only the stdlib otherwise (wave + audioop) so it
doesn't need anything beyond that.
"""

import argparse
import asyncio
import audioop
import json
import sys
import wave
from pathlib import Path

import websockets

DEFAULT_WAV = Path(__file__).parent / "sample_audio" / "two_speakers_sample.wav"
DEFAULT_URL = "ws://localhost:8000/ws/transcribe"
TARGET_SAMPLE_RATE = 16000
CHUNK_SAMPLES = 4096  # matches the extension's ScriptProcessorNode buffer size


def load_pcm16_mono_16khz(wav_path: Path) -> bytes:
    """Reads a WAV file and returns raw 16-bit mono PCM at 16kHz,
    converting sample width / channel count / sample rate as needed."""
    with wave.open(str(wav_path), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        frame_rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if sample_width != 2:
        raw = audioop.lin2lin(raw, sample_width, 2)
        sample_width = 2

    if channels > 1:
        raw = audioop.tomono(raw, sample_width, 0.5, 0.5)

    if frame_rate != TARGET_SAMPLE_RATE:
        raw, _ = audioop.ratecv(raw, sample_width, 1, frame_rate, TARGET_SAMPLE_RATE, None)

    return raw


async def feed(wav_path: Path, url: str, watch_names: list[str], slack_target: str) -> None:
    pcm = load_pcm16_mono_16khz(wav_path)
    chunk_bytes = CHUNK_SAMPLES * 2
    chunk_duration_s = CHUNK_SAMPLES / TARGET_SAMPLE_RATE

    chunks = [pcm[i : i + chunk_bytes] for i in range(0, len(pcm), chunk_bytes)]
    print(f"Streaming {wav_path} — {len(chunks)} chunks (~{len(pcm) / 2 / TARGET_SAMPLE_RATE:.1f}s of audio)")

    async with websockets.connect(url) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "session_init",
                    "watched_user_name_variants": watch_names,
                    "slack_target": slack_target,
                }
            )
        )

        async def send_audio():
            for chunk in chunks:
                await ws.send(chunk)
                await asyncio.sleep(chunk_duration_s)
            print("-- finished sending audio, waiting for trailing transcripts --")
            await asyncio.sleep(5)

        async def receive_events():
            async for raw in ws:
                event = json.loads(raw)
                if "meeting_session_id" in event:
                    print(f"[session] {event['meeting_session_id']}")
                    continue
                if event.get("type") == "error":
                    print(f"[error] {event['message']}")
                    continue
                marker = "partial" if event.get("is_partial") else "FINAL"
                print(f"[{marker}] {event['speaker']}: {event['text']!r} (confidence={event['confidence']:.2f})")

        send_task = asyncio.create_task(send_audio())
        receive_task = asyncio.create_task(receive_events())

        await send_task
        receive_task.cancel()
        try:
            await receive_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav_path", nargs="?", default=str(DEFAULT_WAV))
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--watch-name",
        action="append",
        dest="watch_names",
        default=None,
        help="Name variant to watch for (repeatable). Defaults to a name that won't match real speech.",
    )
    parser.add_argument("--slack-target", default="test@example.com")
    args = parser.parse_args()

    wav_path = Path(args.wav_path)
    if not wav_path.exists():
        print(f"WAV file not found: {wav_path}", file=sys.stderr)
        sys.exit(1)

    watch_names = args.watch_names or ["Nobody Matching This Name"]
    asyncio.run(feed(wav_path, args.url, watch_names, args.slack_target))
