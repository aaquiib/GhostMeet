# AI Meeting Ghost

A Chrome extension that listens to a Google Meet call you have open, detects when a decision or
question needs your input, and notifies you on Slack with a drafted answer you can approve, edit,
or override. Built for a 48-hour hackathon — see [CLAUDE.md](./CLAUDE.md) for full scope,
locked decisions, and build order.

## Backend setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy the env template and fill in real credentials:

```bash
cp ../.env.example .env
# then edit backend/.env
```

Run the backend:

```bash
uvicorn main:app --reload
```

Verify it's up:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

Run tests:

```bash
pytest
```

## Extension setup

1. Open `chrome://extensions` in Chrome.
2. Enable "Developer mode" (top-right toggle).
3. Click "Load unpacked" and select the `extension/` directory.
4. Confirm "AI Meeting Ghost" appears with no errors, and that its icon shows up in the toolbar.
5. Open a `meet.google.com` tab (a real or empty call works), then click the extension's toolbar
   icon — the side panel should open. Nothing starts capturing yet at this point.
6. Click "Start listening". Chrome should prompt for tab-audio capture permission the first
   time; after that the status area should read "Ghost is listening." Confirm you do **not**
   hear the meeting audio play a second time — that would mean the audio graph is wired to
   `audioContext.destination` instead of the silent sink, which must never happen.
7. Click "Stop Ghost" — the status should return to "Ghost is idle." and the Start button should
   reappear. Closing the Meet tab (or navigating it away from meet.google.com) while capturing
   should trigger the same automatic stop.
8. Check `chrome://extensions` → service worker "Inspect views" and the offscreen document's
   console for errors during the above.

## Testing audio capture without the real backend (Phase 1)

`scripts/test_ws_echo.py` is a throwaway WebSocket server that logs the byte length of every
binary PCM frame it receives, so the capture pipeline can be verified before Phase 2's real
backend exists.

```bash
source backend/.venv/bin/activate   # already has `websockets` installed
python3 scripts/test_ws_echo.py     # listens on ws://localhost:8765
```

Point the extension at it — open the side panel, inspect it (right-click → Inspect), and in its
devtools console run:

```js
chrome.storage.local.set({ backendUrl: "ws://localhost:8765" })
```

Click "Start listening" on a `meet.google.com` tab and watch the echo server's terminal log
frame sizes (8192 bytes per frame — 4096 samples × 2 bytes for 16-bit PCM) and a running total
every 10 frames.

Switch back once Phase 2's real backend is up:

```js
chrome.storage.local.set({ backendUrl: "ws://localhost:8000/ws/transcribe" })
// or: chrome.storage.local.remove("backendUrl") to fall back to that same default
```

## Testing the ASR relay (Phase 2)

`/ws/demo` skips real ASR entirely and streams the scripted transcript in
`backend/fixtures/demo_transcript.json` (edit that file, not endpoint code, to change the demo
script). Confirm it with any WebSocket client:

```bash
source backend/.venv/bin/activate
python3 -c "
import asyncio, websockets
async def main():
    async with websockets.connect('ws://localhost:8000/ws/demo') as ws:
        async for msg in ws:
            print(msg)
asyncio.run(main())
"
```

You should see one JSON transcript event roughly every 2 seconds.

`/ws/transcribe` is the real path and needs working AWS Transcribe (or Deepgram) credentials in
`backend/.env` — without them the connection announces a session id and then closes (the AWS
call fails, it falls back to Deepgram, that fails too, and the socket closes with code 1011;
watch the server log for the fallback warning). To test it without a live Google Meet call:

```bash
python3 scripts/feed_wav_file.py                          # uses the bundled sample WAV
python3 scripts/feed_wav_file.py path/to/real_recording.wav
```

`scripts/sample_audio/two_speakers_sample.wav` is a synthetic placeholder (two alternating tones,
not real speech) generated for this repo since no real recording was available — it proves the
streaming/chunking plumbing works, not transcription quality. Swap in a real ~20s two-person
recording to actually exercise ASR and diarization.

## Slack app

`slack-app/manifest.yaml` is the starting app manifest (scopes, interactivity config). Slack app
creation/install is a manual step — see CLAUDE.md conventions.

## Full context

Read [CLAUDE.md](./CLAUDE.md) before making changes — it holds the locked scope, non-negotiable
behaviors, and build order this project follows.
