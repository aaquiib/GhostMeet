# AI Meeting Ghost

**Your always-on second self for meetings you can't fully attend.**

AI Meeting Ghost is a Chrome extension and backend system that listens to a Google Meet call, understands in real time when something is being asked of you, drafts a response grounded in the meeting's own context, and hands it to you on Slack — so a meeting you couldn't fully attend still gets acted on within minutes, not after the fact.

Built end to end in a 48-hour hackathon.

---

## Table of Contents

- [The Idea](#the-idea)
- [How It Works](#how-it-works)
- [Key Features](#key-features)
- [Built With AWS](#built-with-aws)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Repository Structure](#repository-structure)
- [Getting Started](#getting-started)
- [Try It in 2 Minutes (No AWS/Slack Setup Needed)](#try-it-in-2-minutes-no-awsslack-setup-needed)
- [Project Status](#project-status)
- [Learn More](#learn-more)

---

## The Idea

Meetings are where decisions get made and work gets assigned — but you can't be in every meeting, and being physically present doesn't guarantee you're mentally present either. Something gets asked of you, you miss it, and you find out an hour later when someone follows up wondering why you never responded.

AI Meeting Ghost solves this by turning an open (but unattended) Google Meet tab into an active listener on your behalf. It transcribes the conversation in real time, watches for the moments that involve you — a direct question, an action assigned to you, or simply a mention worth knowing about — classifies what kind of moment it is, drafts a suggested response using the meeting's own recent history as context, and sends it to you as a Slack DM you can approve, edit, or reject with one tap. You get pulled back in only when it actually matters, already caught up.

Ghost never joins or "attends" a meeting on its own — it makes an already-open, muted browser tab useful. A human is always in the loop.

## How It Works

Using Ghost is a single action: open the side panel on a Google Meet tab, fill in your name and Slack ID once, and click **Start listening**.

1. **You start listening.** Ghost quietly captures the tab's audio in the background — the meeting keeps playing normally through your speakers, nothing about the call itself changes.
2. **The transcript streams live.** As the conversation unfolds, a live, YouTube-chat-style transcript scrolls up the side panel — newest message at the bottom — so you can glance at what's being said without actively following along.
3. **Ghost listens for you.** The moment someone asks you something, assigns you an action, or references you in a way worth knowing about, Ghost detects it and classifies what kind of moment it is.
4. **A response gets drafted.** For anything actionable, Ghost drafts a suggested reply grounded in the meeting's recent decision history — not a generic "someone mentioned you" ping.
5. **It's checked against policy.** A policy engine gates every notification before it goes out, so certain categories (e.g. hiring decisions) can be blocked from ever being surfaced.
6. **You get pinged on Slack.** A DM arrives with the context and the drafted answer, plus **Approve / Edit / Reject** buttons — so you can step away from the call entirely and still respond the instant something needs you.
7. **You catch up instantly.** The side panel updates live to reflect your response, and the exact transcript line that triggered the notification turns red — so if you jump back into the meeting, you're already caught up, as if you'd been listening the whole time.

## Key Features

- **Live, chat-style transcript feed** — the meeting transcript renders like a live chat (à la YouTube Live), auto-scrolling as new lines arrive, with a "jump to latest" control if you scroll up to read history.
- **Notification-triggering lines highlighted in red** — instantly see which exact words caused a Slack ping, matched against the live transcript.
- **Full situational awareness, not just alerts** — every kind of mention (direct request, action item, informational note, reference, or simple mention) is surfaced, so you always know what's happening in the meeting, not just when you're directly needed.
- **Context-aware drafted answers** — Ghost doesn't just notify you, it proposes an answer, pulling relevant context from the meeting's own recent decision history.
- **One-tap Slack response** — Approve, Edit, or Reject a drafted answer directly from Slack's interactive buttons; your choice reflects back into the extension UI live.
- **Policy-gated notifications** — a Cedar-based policy engine sits in front of every notification and can deny sensitive categories outright, fail-closed on any error.
- **Speaker diarization with manual override** — speakers are automatically distinguished via AWS Transcribe diarization (majority-vote resolved per utterance for accuracy), with a manual override control if a label needs correcting.
- **Durable meeting memory** — the full transcript and every detected decision persist to a Postgres database (Neon), so nothing is lost when the side panel closes and reopens mid-meeting.
- **Debounced, batched notifications** — related moments are coalesced into a single Slack DM instead of a flood of pings.
- **Transparent to the meeting** — the tab audio stays audible locally while being captured; nothing about the call changes for anyone else.

## Built With AWS

AWS powers the entire speech-to-text layer: the backend streams live Google Meet audio to **AWS Transcribe Streaming**, which returns real-time, speaker-diarized transcripts as the meeting happens. Speaker labels are resolved per utterance using a majority-vote approach across labeled words (correcting for diarization jitter) and normalized into a consistent identity used throughout the rest of the pipeline — this transcript is the single source of truth for decision detection, the live transcript feed, and persisted meeting history.

The LLM-based decision-classification stage — the two-tier system deciding whether a moment needs your attention, and drafting the suggested response — runs on **AWS Bedrock**, invoking a Claude Haiku model directly through Bedrock's `invoke_model` API. Both the transcription and reasoning-over-transcript layers run on AWS, with Slack notification, policy enforcement, and Postgres persistence built around what those two services produce.

## Architecture

```
 Google Meet tab audio
          │
          ▼
 Chrome Extension  ──(WebSocket, PCM16 audio)──►  FastAPI Backend
 (capture + side                                        │
  panel UI)                                              ▼
          ▲                                   AWS Transcribe Streaming
          │                                   (live, speaker-diarized transcript)
          │                                              │
          │                                              ▼
          │                                  Two-tier decision detection
          │                                  (regex name-watch + AWS Bedrock LLM)
          │                                              │
          │                                              ▼
          │                                      Cedar policy gate
          │                                              │
          │                                              ▼
          │                                    Context-aware answer drafting
          │                                              │
          │                        ┌─────────────────────┼─────────────────────┐
          │                        ▼                                           ▼
          │                Slack DM (interactive                    Postgres / Neon
          │                Approve/Edit/Reject)                (transcript + decision history)
          │                        │
          └──── live WebSocket push (status updates, transcript) ────┘
```

The backend is deliberately split into two bounded modules with a shared core between them:

- **`transcription/`** — owns everything from raw audio to a finished transcript event. AWS-specific code lives only here.
- **`notifications/`** — owns everything from an accepted decision to an outbound Slack DM and the inbound button click that comes back. Slack-specific code lives only here.
- **A shared core** (decision detection, persistence, database, config) sits between them and knows about neither AWS nor Slack directly — keeping the transcription and notification concerns independently testable and swappable.

## Tech Stack

| Layer | Technology |
|---|---|
| Extension | Chrome Manifest V3 — `tabCapture`, offscreen document, side panel |
| Audio processing | Web Audio API (`ScriptProcessorNode`), 16 kHz, 16-bit PCM |
| Backend | FastAPI, WebSockets, Uvicorn |
| Speech-to-text | AWS Transcribe Streaming (speaker diarization) |
| Decision detection & drafting | AWS Bedrock (Claude Haiku) |
| Policy engine | Cedar (`cedarpy`) |
| Notifications | Slack SDK — Block Kit, interactive buttons, webhooks |
| Database | PostgreSQL (Neon), async via SQLAlchemy + `asyncpg` |
| Search | OpenSearch (optional, falls back to Postgres) |

## Repository Structure

```
aws-project/
├── extension/                     # Chrome extension (Manifest V3)
│   ├── manifest.json
│   ├── background.js              # service worker: capture lifecycle, session state
│   ├── offscreen.js               # audio pipeline + WebSocket relay to backend
│   ├── sidepanel.js / .html / .css # live transcript feed + decision UI
│   └── messages.js                # shared cross-context message contract
├── backend/                       # FastAPI server
│   ├── main.py                    # composition root
│   ├── transcription/             # BOUNDARY 1 — audio -> transcript (AWS)
│   ├── notifications/             # BOUNDARY 2 — decision -> Slack DM, and back
│   ├── decision_detector.py       # shared: two-tier detection pipeline
│   ├── decision_store.py          # shared: decision persistence
│   ├── transcript_store.py        # shared: full-transcript persistence
│   ├── database.py / config.py    # shared: Postgres models, settings
│   └── tests/
├── slack-app/                     # Slack app manifest (scopes, interactivity config)
├── scripts/                       # standalone test/demo utilities
├── docker-compose.yml             # local Postgres + OpenSearch
└── CLAUDE.md                      # full technical build log and design decisions
```

## Getting Started

### Prerequisites

- Python 3.12+, Docker (for local Postgres), Google Chrome
- AWS credentials with Transcribe Streaming + Bedrock access (for real transcription/detection)
- A Slack app + bot token (for real Slack delivery)

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Postgres (repo root)
docker compose up -d postgres

# Configure credentials
cp ../.env.example .env
# edit backend/.env with your AWS / Bedrock / Slack / database credentials

# Run
uvicorn main:app --reload

# Verify
curl http://localhost:8000/health   # {"status":"ok"}
```

### Extension

1. Open `chrome://extensions`, enable **Developer mode**.
2. Click **Load unpacked**, select the `extension/` folder.
3. Open a `meet.google.com` tab, click the extension icon to open the side panel.
4. First time only: fill in your name and Slack ID on the setup screen, **Save & Continue**.
5. Click **Start listening** — grant tab-audio capture permission when prompted.

### Slack App

`slack-app/manifest.yaml` is the starting app manifest (scopes: `chat:write`, `im:write`, `users:read.email`, plus interactivity enabled). Create/install the app from it in your Slack workspace, point its Interactivity request URL at your backend (e.g. via `ngrok http 8000`), and drop the bot token into `backend/.env`.

## Try It in 2 Minutes (No AWS/Slack Setup Needed)

The backend ships with a **text-injection demo mode** that replays a scripted transcript without needing real AWS credentials — the fastest way to see the full pipeline run:

```bash
cd backend && source .venv/bin/activate
python3 -c "
import asyncio, json, websockets
async def main():
    async with websockets.connect('ws://localhost:8000/ws/demo') as ws:
        await ws.send(json.dumps({
            'type': 'session_init',
            'watched_user_name_variants': ['Sarah'],
            'slack_target': 'you@example.com',
        }))
        async for msg in ws:
            print(msg)
asyncio.run(main())
"
```

Point the extension at `/ws/demo` (from the side panel's devtools console: `chrome.storage.local.set({ backendUrl: "ws://localhost:8000/ws/demo" })`) to watch the same scripted meeting play out live in the transcript feed and decision list.

## Project Status

The full pipeline — audio capture → diarized transcription → decision detection → policy check → drafted answer → Slack delivery → live UI update → persistence — is built and has been verified end to end against **real** AWS Transcribe, AWS Bedrock, Neon Postgres, and Slack, not just mocks. The extension side panel reflects live and historical state without a manual refresh, including a live-updating transcript feed with retroactive highlighting of notification-triggering lines.

## Learn More

[CLAUDE.md](./CLAUDE.md) holds the complete technical build log: every locked scope decision, non-negotiable behavior, module boundary, and the reasoning behind every non-obvious engineering choice made while building this — useful for anyone digging into the implementation in depth.
