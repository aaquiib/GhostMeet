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


async def feed(wav_path: Path, url: str) -> None:
    pcm = load_pcm16_mono_16khz(wav_path)
    chunk_bytes = CHUNK_SAMPLES * 2
    chunk_duration_s = CHUNK_SAMPLES / TARGET_SAMPLE_RATE

    chunks = [pcm[i : i + chunk_bytes] for i in range(0, len(pcm), chunk_bytes)]
    print(f"Streaming {wav_path} — {len(chunks)} chunks (~{len(pcm) / 2 / TARGET_SAMPLE_RATE:.1f}s of audio)")

    async with websockets.connect(url) as ws:

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
    args = parser.parse_args()

    wav_path = Path(args.wav_path)
    if not wav_path.exists():
        print(f"WAV file not found: {wav_path}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(feed(wav_path, args.url))
