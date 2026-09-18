"""
Owns the Cedar authorization check that gates every outbound Slack
notification: loads policies/decisions.cedar via cedarpy once at
import time, classifies a decision's type by keyword, and runs the
authorization check for it. A deny (or any failure — this fails
closed) means the decision is logged and nothing is sent.
"""

import logging
from pathlib import Path

import cedarpy

logger = logging.getLogger("ghost.cedar")

_POLICY_PATH = Path(__file__).parent / "policies" / "decisions.cedar"


def _load_policy_set(path: Path) -> cedarpy.PolicySet:
    return cedarpy.PolicySet.from_str(path.read_text())


# Parsed once at import time — never re-parsed per decision. A plain
# module global (not wrapped in a function) so tests can simulate a
# broken policy file by monkeypatching this directly: cedarpy's
# is_authorized() accepts either a pre-parsed PolicySet or a raw
# string it parses internally, so swapping this for a malformed string
# exercises the exact same failure path a corrupt decisions.cedar
# would at startup.
_POLICY_SET: "cedarpy.PolicySet | str" = _load_policy_set(_POLICY_PATH)

# Keyword -> decision type, checked case-insensitively against the
# decision text. First match wins; "general" is the fallback.
_DECISION_TYPE_KEYWORDS: dict[str, list[str]] = {
    "budget_approval": ["budget", "spend", "cost", "pricing", "invoice"],
    "deployment": ["deploy", "release", "ship", "rollout", "production"],
    "hiring": ["hire", "hiring", "candidate", "offer letter", "headcount"],
}


def classify_decision_type(decision_text: str) -> str:
    lowered = decision_text.lower()
    for decision_type, keywords in _DECISION_TYPE_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return decision_type
    return "general"


def check_decision_policy(user_id: str, action: str, decision_type: str) -> dict:
    """Runs the Cedar authorization check. Fails closed: ANY exception
    (malformed policy, a cedarpy internal error, anything at all)
    returns {"allowed": False, "reason": "..."} rather than
    propagating — a broken policy check must never accidentally let a
    notification through. Every check (allow or deny) is logged."""
    try:
        request = {
            "principal": {"type": "User", "id": user_id},
            "action": {"type": "Action", "id": action},
            "resource": {"type": "DecisionType", "id": decision_type},
            "context": {},
        }
        entities = [
            {
                "uid": {"type": "DecisionType", "id": decision_type},
                "attrs": {"decision_type": decision_type},
                "parents": [],
            }
        ]
        result = cedarpy.is_authorized(request, _POLICY_SET, entities)
        reason = ", ".join(result.diagnostics.reasons) or "no matching policy"
        outcome = {"allowed": bool(result.allowed), "reason": reason}
    except Exception as exc:
        outcome = {"allowed": False, "reason": f"policy check failed: {exc}"}

    logger.info(
        "cedar check: user_id=%s action=%s decision_type=%s -> allowed=%s reason=%s",
        user_id,
        action,
        decision_type,
        outcome["allowed"],
        outcome["reason"],
    )
    return outcome
