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

## Slack app

`slack-app/manifest.yaml` is the starting app manifest (scopes, interactivity config). Slack app
creation/install is a manual step — see CLAUDE.md conventions.

## Full context

Read [CLAUDE.md](./CLAUDE.md) before making changes — it holds the locked scope, non-negotiable
behaviors, and build order this project follows.
