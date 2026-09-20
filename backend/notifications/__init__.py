"""
Slack notification boundary — everything that turns an accepted
DecisionRecord into an outbound Slack DM, and everything that comes
back from Slack.

Inside: the Cedar authorization gate (cedar_policy.py), the drafted
answer's second LLM call (answer_drafter.py), Block Kit composition and
the outbound DM (slack_notifier.py), the inbound interactivity webhook
(slack_webhook.py), and the pipeline that sequences them
(notification_pipeline.py).

What crosses this boundary inward is DecisionRecord; nothing in here
knows about audio, ASR providers, or TranscriptEvents. Exactly two
names are needed by main.py to wire the whole side up, so those are
what this package exposes.
"""

from notifications.notification_pipeline import make_notification_pipeline
from notifications.slack_webhook import router as slack_webhook_router

__all__ = ["make_notification_pipeline", "slack_webhook_router"]
