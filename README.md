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
   icon — the side panel should open.
6. First time only: fill in the identity setup screen — your name (Tier 1's watch-name regex uses
   this), optional other name variants, and your Slack user ID or email — then "Save & Continue".
   This is stored in `chrome.storage.local` and only asked once; Start is hidden until it's filled
   in. Nothing starts capturing yet at this point.
7. Click "Start listening". Chrome should prompt for tab-audio capture permission the first
   time; after that the status area should read "Ghost is listening." Confirm you do **not**
   hear the meeting audio play a second time — that would mean the audio graph is wired to
   `audioContext.destination` instead of the silent sink, which must never happen.
8. Try the speaker-override input (below the status area) — enter a label like `spk_0` and a name,
   click Apply. Check the backend log for "speaker override applied" to confirm it reached the
   server mid-session.
9. Click "Stop Ghost" — the status should return to "Ghost is idle." and the Start button should
   reappear. Closing the Meet tab (or navigating it away from meet.google.com) while capturing
   should trigger the same automatic stop.
10. Check `chrome://extensions` → service worker "Inspect views" and the offscreen document's
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
script). Every connection — demo or real — now requires a `session_init` control message first
(Phase 4; see below), so a bare client needs to send that before anything else. Confirm it with
any WebSocket client:

```bash
source backend/.venv/bin/activate
python3 -c "
import asyncio, json, websockets
async def main():
    async with websockets.connect('ws://localhost:8000/ws/demo') as ws:
        await ws.send(json.dumps({
            'type': 'session_init',
            'watched_user_name_variants': ['Sarah'],  # matches the demo script
            'slack_target': 'you@example.com',
        }))
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
python3 scripts/feed_wav_file.py --watch-name Aman --watch-name "Aman Kumar" --slack-target you@example.com
```

It sends the mandatory `session_init` handshake (Phase 4) before any audio; `--watch-name` is
repeatable and defaults to a name that won't match real speech, since this script is for
exercising ASR — pass your own name too if you also want it to exercise decision detection.

`scripts/sample_audio/two_speakers_sample.wav` is a synthetic placeholder (two alternating tones,
not real speech) generated for this repo since no real recording was available — it proves the
streaming/chunking plumbing works, not transcription quality. Swap in a real ~20s two-person
recording to actually exercise ASR and diarization.

## Testing decision detection (Phase 3)

`backend/decision_detector.py` turns final transcript events into batched `DecisionRecord`s. Its
own unit tests (`backend/tests/test_decision_detector.py`) fake the LLM call, so they run fast
and need no credentials:

```bash
cd backend && source .venv/bin/activate && pytest tests/test_decision_detector.py -v
```

Detection is off by default per session — `DecisionPipeline.set_watch_names(meeting_id, names)`
must be called with the names to watch for, or Tier 1 never matches anything. As of Phase 4 this
is driven by the `session_init` handshake below, for both `/ws/demo` and real `/ws/transcribe`
sessions.

With `ASR_PROVIDER` and `LLM_API_KEY` both pointing at real, working credentials, running
`scripts/feed_wav_file.py` against a real two-person recording exercises the full path — audio →
transcript → decision detection — and any detected batch gets logged to the server console and
persisted to SQLite (`backend/ghost.db`, gitignored — see Phase 5 below). A wrong/missing
`LLM_API_KEY` fails gracefully: Tier 2 logs the failure and the window is treated as "not a
decision" rather than crashing the session.

## Session identity + Cedar/Slack notification (Phase 4)

Every `/ws/transcribe` and `/ws/demo` connection now requires a `session_init` control message as
its first client-sent frame:

```json
{
  "type": "session_init",
  "watched_user_name_variants": ["Aman", "Aman Kumar"],
  "slack_target": "aman@example.com"
}
```

Missing it, or sending anything else first, gets a `{"type": "error", ...}` reply and the socket
closes (code 1008) — there's no silent fallback to a hardcoded name. A `speaker_override` control
message (`{"type": "speaker_override", "label": "spk_0", "name": "Priya"}`) can follow at any
point later in the same connection and takes effect immediately.

Run the Phase 4 tests (all fake the LLM/Slack calls, so no credentials needed):

```bash
cd backend && source .venv/bin/activate
pytest tests/test_session_init.py tests/test_notification_pipeline.py tests/test_slack_webhook.py -v
```

To actually receive a Slack DM end-to-end, you'll need:

1. Real `SLACK_BOT_TOKEN`/`SLACK_SIGNING_SECRET` in `backend/.env`, and a Slack app installed from
   `slack-app/manifest.yaml` (includes `chat:write`, `im:write`, `users:read.email`).
2. `ngrok http 8000` (or similar) to get a public URL, and the Slack app's Interactivity request
   URL updated to `https://<ngrok-url>/slack/interaction` — a manual step in the Slack app config
   (see CLAUDE.md conventions).
3. A real `LLM_API_KEY` — both decision detection (Tier 2) and answer drafting call it.
4. A `session_init` whose `watched_user_name_variants` actually appears in the transcript (the
   bundled demo script says "Sarah"), and a real `slack_target` (your Slack user ID or email) to
   receive the DM.

`backend/policies/decisions.cedar` forbids `hiring`-classified decisions as a working example of
a real deny — edit it to add more rules; `cedar_policy.check_decision_policy` fails closed on any
parse/evaluation error, so a broken policy file blocks notifications rather than allowing them
through.

## Persistence + search (Phase 5)

Decisions persist in SQLite (`backend/ghost.db`, gitignored — schema created automatically on
startup, WAL journal mode so concurrent writes from decision creation and the Slack webhook don't
lock each other out). `backend/decision_store.py`'s `DecisionStore` interface is unchanged from
Phase 4 — `SQLiteDecisionStore` just replaced the interim `JSONLDecisionStore`.

```bash
cd backend && source .venv/bin/activate
pytest tests/test_database.py -v          # concurrency + lifecycle, no credentials needed
```

OpenSearch is optional and additive — SQLite stays the source of truth either way. For local dev:

```bash
docker compose up -d       # repo root — the only place this project uses Docker
```

Then set `OPENSEARCH_HOST=http://localhost:9200` in `backend/.env` (leave `OPENSEARCH_USER`/
`OPENSEARCH_PASSWORD` blank — the compose file disables the security plugin for local-dev
simplicity). With it unset or unreachable, `GET /search?q=` transparently falls back to a SQLite
`LIKE` query — nothing hard-fails.

```bash
curl "http://localhost:8000/search?q=ship%20date"
```

```bash
pytest tests/test_opensearch_client.py -v   # mapping/document/query shape, mocked client
pytest tests/test_search_endpoint.py -v     # real fallback (genuinely unreachable host, no
                                             # Docker needed) + a real OpenSearch check that
                                             # skips itself if docker-compose isn't up
```

> This repo's own sandbox can't run Docker (nested containerization isn't available, and image
> pulls are blocked by egress policy), so `opensearch_client.py` is verified here against a fake
> client plus a real unreachable-host fallback check — not against a live OpenSearch. If you have
> Docker available, `docker compose up -d` then `pytest tests/test_search_endpoint.py -v` will
> also exercise the real integration path.

## Side panel UI (Phase 6)

The side panel now reflects live and historical decision state without a manual refresh. After
Start/Stop and the setup screen (Phase 1/4), it renders:

- The **listening indicator** — a pulsing cyan dot + "Ghost is listening" whenever capture is
  connected/reconnecting, with the session id and a "since HH:MM:SS" line beneath.
- A **decision list** — one card per decision (speaker, timestamp, decision text, a colored status
  badge: cyan `pending`, green `approved`, gray `rejected`, red `denied_by_policy`, violet
  `answered_live`). All decision-derived text is inserted via `textContent`, never `innerHTML`.
- A **triggered/answered view** — when a decision moves to `approved`/`answered_live`, it's pinned
  above the list with the exact question, the drafted answer, and an "Answer sent"/"Take over
  live" line, mirroring the Slack DM.
- A **backend-unreachable message + retry button** if the initial `GET /decisions` hydration call
  fails, instead of silently showing an empty list.
- A **search box** (P2) — 300ms-debounced `GET /search?q=&meeting_id=`; clearing the query restores
  the full list from in-memory state without refetching.

**How live pushes reach the panel:** `backend/session_connections.py` is a registry mapping
`meeting_session_id` -> the live WebSocket on `/ws/transcribe`/`/ws/demo`, so code that doesn't
hold that WebSocket directly (`notification_pipeline.py`, `slack_webhook.py`) can still push to
it. After Cedar + drafting complete, `notification_pipeline.py` pushes
`{"type": "decision_batch", "meeting_id": ..., "decisions": [...]}` — every decision in the batch,
allowed or denied, each carrying a `drafted_answer` key (allowed only; not part of `DecisionRecord`
itself — Phase 3/4 deliberately keep drafts un-persisted, so this is an extra key on the pushed
JSON only, reusing the same draft already computed for Slack). When a Slack button click updates a
decision's status, `slack_webhook.py` pushes
`{"type": "decision_status_update", "decision_id": ..., "status": ..., "approved_by": ...}` the
same way. `offscreen.js` relays both (as `DECISION_BATCH`/`DECISION_STATUS_UPDATE`
`chrome.runtime` messages — see `extension/messages.js`) to the side panel, along with the
backend-confirmed session id (`SESSION_ID_ASSIGNED`) the very first time `/ws/transcribe` responds
with `{"meeting_session_id": ...}` — that id (stored under the new `chrome.storage.session` key
`ghostPanelSessionId`, distinct from `background.js`'s own client-generated `ghostSession` id,
which the backend never actually uses) is what `GET /decisions`/`GET /search` calls are scoped by.
Demo mode (`/ws/demo`) never sends that announcement, so the panel picks up `demo-meeting` from the
first `decision_batch` push instead.

`GET /decisions?meeting_id=` (new this phase) hydrates the panel's list on open/reopen; it's a thin
wrapper over the same `decision_store.get_recent` Phase 5 already had, added because the panel
needs a way to backfill state that predates it being open — live pushes on top of that, not instead
of it. Historical decisions from it never carry `drafted_answer` (never persisted); only ones
pushed live while the panel is open do.

`extension/manifest.json` adds `http://localhost:8000/*` to `host_permissions` so the panel's
`fetch()` calls to `GET /decisions`/`GET /search` aren't blocked by CORS — Chrome extension pages
with a matching host permission bypass CORS entirely, so `main.py` didn't need `CORSMiddleware`.

Run the Phase 6 backend tests (the new push wiring; no browser needed):

```bash
cd backend && source .venv/bin/activate
pytest tests/test_session_connections.py tests/test_notification_pipeline.py tests/test_slack_webhook.py -v
```

Manual panel tests (no automated harness on the extension side):

1. Point `backendUrl` at `/ws/demo` (see the Phase 2 section above), open the side panel, click
   Start — a decision card should appear within ~2s of the scripted transcript triggering one,
   with the right speaker/text/`pending` badge.
2. Approve a decision from a real (or test) Slack DM — the panel should switch to the
   triggered/answered view without a manual refresh or panel reopen.
3. Stop the backend, then open the panel — the "Can't reach Ghost's backend" message and Retry
   button should appear instead of a blank list; restart the backend and click Retry.
4. Type a query matching one decision's text into the search box — only that decision should show;
   clear the query — the full list returns without an extra network call.
5. In demo mode, edit `backend/fixtures/demo_transcript.json` so a decision's text contains
   `<b>test</b>` — it should render as the literal string in the card, not as bold markup.

## Slack app

`slack-app/manifest.yaml` is the starting app manifest (scopes, interactivity config). Slack app
creation/install is a manual step — see CLAUDE.md conventions.

## Full context

Read [CLAUDE.md](./CLAUDE.md) before making changes — it holds the locked scope, non-negotiable
behaviors, and build order this project follows.
