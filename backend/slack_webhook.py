"""
Owns the inbound side of Slack: the FastAPI route(s) that receive
interactivity payloads (button clicks: approve/edit/override) from
Slack's request URL, verify the request signature against
SLACK_SIGNING_SECRET, and route the response back into the app.
"""
