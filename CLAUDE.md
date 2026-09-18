# AI Meeting Ghost

A Chrome extension that listens to a Google Meet call the user has open, detects when a decision or question needs their input, and notifies them on Slack with a drafted answer they can approve, edit, or override.

Built for a 48-hour hackathon. Optimize for a working, rehearsed demo over completeness.

## Scope (locked — do not expand without asking)

- **Platform:** Google Meet only. No Zoom, no Teams.
- **Notifications:** Slack DMs only. No Chrome desktop alerts.
- **ASR:** AWS Transcribe Streaming is primary. Deepgram is the fallback — same downstream shape, just a different provider, swappable if Transcribe setup breaks.
- **Demo strategy:** Text-injection (pre-scripted fake transcripts via `/ws/demo`) is the primary demo path. Live audio through a real Meet call is a bonus secondary path — if it's flaky, fall back to text-injection without hesitation.
- **Confidence threshold:** Hardcoded at `0.7`. No user-facing "trust dial" slider in v1.
- **Audio processing:** `ScriptProcessorNode`, not `AudioWorklet` — simpler for the timeline, fine for a demo.
- **Speaker mapping:** Infer names from self-introductions ("I'm ___" / "This is ___"), fall back to generic labels (`spk_0`, `spk_1`) otherwise.
- **Diarization:** Use Transcribe's speaker-label diarization for a single mixed stream. Do **not** use channel identification — that requires true stereo with one speaker per channel, which tab audio isn't.

## Key assumption to hold consistently

Ghost does not dial into a meeting unattended. "Can't attend" means a muted browser tab stays open with the extension running — the extension makes that tab useful, it doesn't join a call on its own. Don't write code or copy that implies autonomous meeting-joining.

## Non-negotiable behaviors

- **No echo/feedback:** processed audio must route to a silent sink, never `audioContext.destination`.
- **Debounce before notifying:** hold the first detected decision for a 10–15s coalescing window; batch anything else that lands in it into one Slack DM, not one per detection.
- **Drafted answer required:** before sending the Slack DM, query past decisions (OpenSearch, or SQLite early on) for relevant context and draft a suggested answer via a second LLM call. This is the core differentiator — don't ship a plain notifier.
- **Cedar policy check gates every notification.** "Deny" means log the decision, send nothing.
- **Stop condition:** manual "Stop Ghost" control in the side panel, plus detecting the Meet tab closing.
- **Consent indicator:** a persistent "Ghost is listening" element in the side panel whenever capture is active.

## Repo structure

```
ghost/
├── extension/          # Chrome extension (Manifest V3)
│   ├── manifest.json
│   ├── background.js
│   ├── offscreen.html
│   ├── offscreen.js
│   ├── sidepanel.html
│   └── sidepanel.js
├── backend/             # FastAPI server
│   ├── main.py
│   ├── transcribe_handler.py
│   ├── decision_detector.py
│   ├── cedar_policy.py
│   ├── slack_notifier.py
│   ├── slack_webhook.py
│   ├── opensearch_client.py
│   ├── database.py
│   └── demo_mode.py
└── slack-app/           # Slack app configuration
```

## Tech stack

| Layer | Technology |
|---|---|
| Extension | Chrome Manifest V3, tabCapture, offscreen, sidePanel |
| Audio processing | ScriptProcessorNode, 16kHz, 16-bit PCM |
| Backend | FastAPI, WebSockets, Uvicorn |
| ASR | AWS Transcribe Streaming (Deepgram fallback) |
| LLM | Strands Agent (or Claude 3 Haiku for latency) |
| Policy | Cedar (cedarpy) |
| Notifications | Slack SDK (Block Kit, interactive buttons) |
| Database | SQLite + SQLAlchemy (async) — build first, P0 |
| Search | OpenSearch — add after core loop works, P1 |
| Deployment | Local + ngrok for the Slack webhook |

## Build order and priority

Build phase by phase, in this order. Each phase has its own exit criteria — treat those as the definition of done, not "looks like it works."

0. Architecture lock + repo scaffold
1. Audio capture pipeline (extension side)
2. Streaming relay + ASR (backend side)
3. Decision detection (LLM, two-tier trigger, debounce)
4. Cedar policy + Slack (incl. drafted answer)
5. Database + OpenSearch (SQLite first, OpenSearch second)
6. Side panel UI (listening state, triggered/answered state, search — search is P2, cut first if short on time)
7. Demo rehearsal — run this in a clean context, full end-to-end, twice in a row

## Settled decisions from completed phases

**Phase 1 (extension audio capture):**

- **Message protocol:** every `chrome.runtime.sendMessage` payload is `{ type, target, ...data }`.
  `type` is always one of the named constants in `extension/messages.js`
  (`START_CAPTURE`, `STOP_CAPTURE`, `CAPTURE_STARTED`, `CAPTURE_STOPPED`, `CAPTURE_ERROR`,
  `CONNECTION_STATUS`), never a raw string. `target` is `'background'`, `'offscreen'`, or
  `'sidepanel'` and exists because Chrome's messaging is a broadcast bus — every context with a
  listener receives every message, so each listener filters on `target` to avoid mis-handling or
  self-looping on messages it sent itself.
- **Capture state:** background.js keeps state in `chrome.storage.session` under the key
  `ghostSession`, shape `{ status: 'idle'|'capturing'|'error', meetingSessionId: string|null,
  tabId: number|null }`. Session storage (not in-memory globals) so a service-worker restart
  mid-call doesn't lose track of an in-progress session.
- **Backend URL:** offscreen.js reads it from `chrome.storage.local` key `backendUrl`, default
  `ws://localhost:8000/ws/transcribe`. Unset/empty falls back to the default.
- **PCM wire format:** raw binary WebSocket frames (not JSON-wrapped), Int16 PCM, mono, 16kHz,
  4096 samples (8192 bytes) per frame. Phase 2's backend WS handler needs to expect exactly this,
  not a JSON envelope.
- **Reconnection:** offscreen.js retries a dropped backend connection up to 3 times with a 3s
  delay between attempts; the 3rd failure broadcasts `CAPTURE_ERROR` ("Connection lost") and
  tears down the whole capture pipeline (mic, audio graph, socket) rather than leaving it running
  with nowhere to send audio.

## Conventions

- One commit per completed phase, not mid-phase.
- When a phase settles a decision that affects later phases (e.g. the final diarization implementation, the debounce window length), update this file so later phases inherit it instead of re-deciding it.
- Manual, human-only steps — Slack app creation/install, AWS IAM and budget alarms — are done outside of Claude Code. Ask for the credentials/config rather than trying to provision them.
- If a proposed change would expand scope (a platform beyond Meet, a notification channel beyond Slack, a UI control beyond what's listed), flag it before building instead of adding it silently.
