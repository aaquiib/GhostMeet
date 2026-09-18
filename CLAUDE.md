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

**Phase 2 (backend streaming relay + ASR):**

- **TranscriptEvent shape** (`backend/asr_base.py`), identical from both providers: `text: str`,
  `speaker: str` (`"spk_N"` from provider diarization, or `"unknown"`; name inference from
  self-intros is not done here — that's Phase 3's job), `timestamp: datetime` (wall-clock UTC at
  the moment the event was finalized, not audio-relative seconds), `confidence: float`,
  `is_partial: bool`, `meeting_id: str`.
- **What reaches the client:** only final (`is_partial=False`) events, sent over `/ws/transcribe`
  as JSON via `websocket.send_json(event.model_dump(mode="json"))`. Partials are produced
  internally but dropped before the WebSocket — Phase 3 only ever sees finals.
- **`/ws/transcribe` session handling:** optional `?session_id=` query param; omit it and the
  server generates one and sends `{"meeting_session_id": "..."}` as the first message so the
  client can persist it for reconnects. Supplying one is trusted as-is (no server-side session
  registry yet — that's Phase 5) and always opens a NEW provider stream under that meeting_id.
- **`/ws/demo`:** streams `backend/fixtures/demo_transcript.json` verbatim as TranscriptEvents,
  one every 2s, `meeting_id="demo-meeting"`, `is_partial` always `False`. Edit that JSON file to
  change the scripted demo, not the endpoint code.
- **ASR_PROVIDER fallback:** `aws` (default) automatically falls back to Deepgram if
  `AWSTranscribeProvider.start()` raises; `deepgram` uses Deepgram directly with no fallback.
  Both require real credentials to actually produce transcripts — without them `get_asr_provider`
  raises and `/ws/transcribe` closes with code 1011 after announcing the session id.
- **Logging:** every log line for a `/ws/transcribe` or `/ws/demo` connection goes through
  `MeetingLoggerAdapter` (`backend/asr_base.py`), which prefixes `[meeting_session_id=...]` to the
  message text — not a `%(meeting_session_id)s` field in the global formatter, which would
  `KeyError` on any other logger (uvicorn's, the AWS/Deepgram SDKs') that doesn't carry it.

**Phase 3 (decision detection):**

- **LLM model:** CLAUDE.md's "Claude 3 Haiku" is retired — Tier 2 uses `claude-haiku-4-5`
  (`backend/decision_detector.py`, constant `LLM_MODEL`, public — Phase 4's answer_drafter.py
  reuses it and `LLM_TIMEOUT_SECONDS`), the current latency-optimized model in the same tier.
  Called via the `anthropic` SDK (added to `requirements.txt`, pinned `1.6.0`) with
  `output_config={"format": {"type": "json_schema", ...}}` for structured JSON, wrapped in
  `asyncio.wait_for(..., timeout=4.0)`.
- **Per-session name watching is not automatic:** `DecisionPipeline.set_watch_names(meeting_id,
  names)` must be called before Tier 1 will ever match anything. Phase 4's session_init handshake
  (see below) is what actually calls this for real sessions now — see that section, not this one,
  for how identity reaches a session.
- **Buffering window:** carry-forward is literally prepended into the next window's buffer (not a
  separate context list) — `state.buffer = window_events[-3:]` after each close — so both Tier 1's
  regex and Tier 2's LLM call see carried lines alongside new ones. Window closes at 30s elapsed
  (by event timestamp, not wall clock) or 10 segments, whichever first.
- **Debounce timer is real wall-clock**, `asyncio.sleep(settings.debounce_window_seconds)` (12s
  default), separate from the window-closing logic above. Tests shorten
  `settings.debounce_window_seconds` directly (it's a mutable pydantic-settings singleton) rather
  than waiting 12 real seconds.
- **Never blocks the transcript stream:** `DecisionPipeline.process_transcript_event` only ever
  blocks synchronously on cheap buffering + a regex check; Tier 2's LLM call and everything after
  it (dedup, batching) runs as an internally-tracked background `asyncio.Task`. `main.py`'s
  `transcript_loop` awaits it inline — that's safe precisely because it never blocks meaningfully.
- **DecisionRecord** (`backend/decision_detector.py`) is the shape Phase 4/5 consume: `id`
  (server-generated uuid4), `meeting_id`, `decision_text`, `speaker` (resolved via SpeakerMapper —
  a name if self-introduced, else the raw `spk_N` label), `requires_action_from`, `context`,
  `confidence`, `urgency`, `timestamp`, `status` (default `"pending"`). No Slack/DB-specific
  fields — don't add any when wiring Phase 4.
- **Output callback:** `DecisionPipeline(on_decision_batch=...)` — default
  (`default_on_decision_batch`) logs each batch and appends to `backend/decisions_log.jsonl`
  (gitignored, placeholder only). Phase 4 replaces the callback the shared `decision_pipeline`
  singleton (`backend/decision_detector.py`) is constructed with; nothing else in this file needs
  to change.

**Phase 4 (session identity + Cedar/Slack notification):**

- **session_init handshake:** the first message on every `/ws/transcribe` and `/ws/demo`
  connection (after the server's own `{"meeting_session_id": ...}` announcement on
  `/ws/transcribe`) must be
  `{"type": "session_init", "watched_user_name_variants": [...], "slack_target": "..."}` from the
  client. Missing/malformed/absent → the server sends `{"type": "error", "message": "..."}` and
  closes with code 1008. No silent fallback to a hardcoded name, ever — see
  `main.py`'s `_handshake_session_init`. This is what actually calls
  `decision_pipeline.set_watch_names`/`set_slack_target` for a session now.
- **Per-session identity lives in `DecisionPipeline`'s `_SessionState`** (`slack_target` field,
  alongside the existing `watch_names_pattern`), never in the process-wide `Settings` object —
  Settings is shared across every concurrent meeting, so it can't hold a per-meeting value.
  `decision_pipeline.get_slack_target(meeting_id)` is how the notification pipeline looks it up.
- **Control messages after the handshake:** `/ws/transcribe`'s receive loop now inspects each
  frame's raw type (`websocket.receive()`, not `receive_bytes()` — Phase 2's assumption that every
  frame is audio is obsolete) so binary audio and JSON control messages (currently just
  `{"type": "speaker_override", "label": "spk_0", "name": "Priya"}`) can interleave.
  `decision_detector.handle_control_message(meeting_id, payload, log)` routes these — both
  `/ws/transcribe` and `/ws/demo` call it, so routing logic lives in one place.
- **Extension side:** `chrome.storage.local` key `ghostIdentity` = `{ name, nameVariants,
  slackTarget }`, written by the sidepanel's one-time setup screen (gates the Start button).
  `background.js` reads it and includes `watchedUserNameVariants`/`slackTarget` on the
  `START_CAPTURE` message to offscreen.js, which sends `session_init` as the first WS frame on
  every (re)connection — including reconnects, since the backend expects it as the first frame of
  every new connection, not just the session's first. A new `SPEAKER_OVERRIDE` message type
  (`extension/messages.js`) carries a speaker-override submission from sidepanel through
  background to offscreen, which sends it as a `speaker_override` control message immediately.
- **DecisionStoreLike (structural typing, not an import):** `decision_detector.py` defines a
  `Protocol` with just `create()` rather than importing `decision_store.py`, because
  `decision_store.py` needs `DecisionRecord` from `decision_detector.py` — importing both
  directions would cycle. `main.py` is what actually connects them at startup
  (`decision_pipeline.set_decision_store(decision_store)`), not either module importing the other.
  The same reasoning is why `main.py` (not `decision_detector.py`) constructs
  `make_notification_pipeline(decision_store)` and assigns it to
  `decision_pipeline.on_decision_batch`.
- **JSONLDecisionStore is append-only, "latest line wins":** `update_status` doesn't edit
  `decisions_log.jsonl` in place — it appends the record again with the new status. The in-memory
  index (rebuilt from the file at startup) keeps only the latest line per `id`, so this gives
  correct current-state semantics without needing in-place file edits. `approved_by` is accepted
  and logged but not persisted on the record itself — Phase 3's `DecisionRecord` intentionally has
  no field for it; Phase 5's real schema can add one.
- **Cedar policy set is a plain reassignable module global** (`cedar_policy._POLICY_SET`), parsed
  once at import — not wrapped in a function — specifically so tests can simulate a corrupt
  `decisions.cedar` by monkeypatching it to a malformed string (`cedarpy.is_authorized` accepts a
  raw string and parses it internally, so this hits the exact same failure path a bad file would).
  `check_decision_policy` fails closed on literally any exception.
  `policies/decisions.cedar` forbids `resource.decision_type == "hiring"` as the one example rule;
  everything else is permitted by default — edit freely.
- **Decision persistence timing:** `decision_store.create()` is called as soon as a decision is
  accepted (in `_classify_and_batch`, right after the dedup check passes) — not delayed until the
  debounce timer fires. The timer is purely a notification-batching concern; persistence
  shouldn't wait on it. A persistence failure is logged but doesn't block batching/notification.
- **`answer_drafter.draft_answer`'s "search"** is keyword-overlap re-ranking of
  `decision_store.get_recent(meeting_id, limit=10)`'s results in memory — not a second store
  method. Keeps the store interface to exactly the 3 methods in the spec; Phase 5 swaps the
  ranking step for real OpenSearch relevance search without changing the interface.
- **`python-multipart` added to `requirements.txt`** (pinned `0.0.20`) — required transitively by
  Starlette's `Request.form()`, which `/slack/interaction` needs to parse Slack's
  `application/x-www-form-urlencoded` payload.
- **Slack scopes:** `slack-app/manifest.yaml` needs `users:read.email` in addition to
  `chat:write`/`im:write` — `slack_notifier.py` resolves an email `slack_target` via
  `users.lookupByEmail` before DMing.

## Conventions

- One commit per completed phase, not mid-phase.
- When a phase settles a decision that affects later phases (e.g. the final diarization implementation, the debounce window length), update this file so later phases inherit it instead of re-deciding it.
- Manual, human-only steps — Slack app creation/install, AWS IAM and budget alarms — are done outside of Claude Code. Ask for the credentials/config rather than trying to provision them.
- If a proposed change would expand scope (a platform beyond Meet, a notification channel beyond Slack, a UI control beyond what's listed), flag it before building instead of adding it silently.
