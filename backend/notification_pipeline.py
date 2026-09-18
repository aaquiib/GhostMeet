"""
Builds the on_decision_batch(meeting_id, decisions) callback that
DecisionPipeline (Phase 3) expects, replacing its placeholder console
logger: Cedar-gates each decision, drafts answers for the allowed ones
concurrently, and sends one Slack message for the whole batch to the
session's captured identity.
"""

import asyncio
import logging

from answer_drafter import draft_answer
from cedar_policy import check_decision_policy, classify_decision_type
from decision_detector import DecisionRecord, decision_pipeline
from decision_store import DecisionStore
from slack_notifier import send_batch_notification

logger = logging.getLogger("ghost.notify")


def make_notification_pipeline(decision_store: DecisionStore):
    async def on_decision_batch(meeting_id: str, decisions: list[DecisionRecord]) -> None:
        allowed: list[DecisionRecord] = []
        denied: list[tuple[DecisionRecord, str]] = []

        for decision in decisions:
            decision_type = classify_decision_type(decision.decision_text)
            policy_result = check_decision_policy(
                user_id=decision.requires_action_from or "unknown",
                action="notify",
                decision_type=decision_type,
            )
            if policy_result["allowed"]:
                allowed.append(decision)
            else:
                denied.append((decision, policy_result["reason"]))

        for decision, reason in denied:
            logger.info(
                "[meeting_id=%s] decision %s denied by policy: %s", meeting_id, decision.id, reason
            )
            try:
                await decision_store.update_status(decision.id, "denied_by_policy", None)
            except Exception:
                logger.exception(
                    "[meeting_id=%s] failed to update denied decision %s status", meeting_id, decision.id
                )

        if not allowed:
            return

        # Independent LLM calls — run concurrently rather than one at
        # a time.
        drafts = await asyncio.gather(*(draft_answer(decision, meeting_id) for decision in allowed))

        slack_target = decision_pipeline.get_slack_target(meeting_id)
        if not slack_target:
            logger.error(
                "[meeting_id=%s] no slack_target captured for this session; cannot notify", meeting_id
            )
            return

        try:
            await send_batch_notification(slack_target, list(zip(allowed, drafts)))
        except Exception:
            logger.exception("[meeting_id=%s] failed to send Slack notification", meeting_id)

    return on_decision_batch
