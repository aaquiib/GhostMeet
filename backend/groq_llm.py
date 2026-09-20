"""
Minimal Groq client that mimics just enough of the `anthropic` SDK's
`.messages.create(...)` surface — the
(model, max_tokens, system, messages, ...) -> response.content[i].type/
.text shape — that decision_detector.py and answer_drafter.py don't
need to change their calling code at all, and existing tests' fake LLM
clients (which already mimic this exact shape to avoid real network
calls in tests) keep working completely unchanged. Only the concrete
class constructed by default changes. Replaces bedrock_llm.py (AWS
Bedrock invocation was blocked at the AWS account level — confirmed via
direct empirical testing with every combination of model ID, inference
profile, and auth mechanism (bearer-token API key and SigV4-signed
credentials both got an identical `ValidationException: Operation not
allowed`) — not fixable from application code, so this project moved to
Groq instead rather than chase an AWS Marketplace/billing issue).

Groq's Chat Completions API is OpenAI-compatible: a flat `messages`
array with `system`/`user`/`assistant` roles (no separate top-level
`system` field the way Anthropic's Messages API has one), and a
response shaped as `choices[0].message.content` (a plain string, not a
list of typed content blocks). This client translates between Anthropic's
shape (what the calling code still uses) and Groq's on every call.
"""

import logging
from types import SimpleNamespace

import httpx

from config import settings

logger = logging.getLogger("ghost.groq")

# Confirmed available on this project's own Groq account via GET
# /openai/v1/models (Groq's catalog changes over time — re-check
# console.groq.com/docs/models or that endpoint if this ever 404s
# "model_not_found" again). openai/gpt-oss-20b is a good balance of
# quality and Groq's signature low latency for classification/drafting.
GROQ_MODEL = "openai/gpt-oss-20b"

_GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

# Backstop only — the real timeout is the asyncio.wait_for(...) wrapper
# already applied at every call site (decision_detector.py,
# answer_drafter.py).
_HTTP_TIMEOUT_SECONDS = 15.0

# gpt-oss-20b is a reasoning model: before writing any actual output it
# spends part of max_tokens on a hidden "reasoning" pass, and that
# spend is highly variable — empirically observed between ~220 and
# ~720 tokens out of a 1024 budget for the same prompt across repeated
# calls. On an unlucky call, reasoning alone can eat most of the
# budget, leaving too little left to finish valid JSON — which is what
# actually caused live json_validate_failed 400s from Groq's own
# response_format validator, not a malformed prompt. "low" cuts
# reasoning spend to a tight, consistent ~80-100 tokens in the same
# test, which both removes the starvation risk and reduces latency —
# verified not to degrade classification quality (mention_type/
# requires_action_from/confidence stayed consistent and correct across
# repeated calls at "low"). Applies to every call, including
# answer_drafter.py's plain-prose draft, which needs no deep reasoning
# either and benefits from the same latency reduction under the shared
# LLM_TIMEOUT_SECONDS budget.
_REASONING_EFFORT = "low"

# The one Groq error code worth retrying: response_format=json_object
# occasionally rejects its own generation as invalid/incomplete JSON
# and returns 400 instead of content. A second attempt almost always
# succeeds (different sampling), so retry exactly once rather than
# losing the whole classification window — kept as a safety net on top
# of the reasoning_effort fix above, in case it still happens
# occasionally. Every caller already wraps this call in its own
# asyncio.wait_for budget, so a slow retry just degrades to that
# existing timeout handling rather than a new failure mode.
_JSON_VALIDATION_RETRY_ERROR_CODE = "json_validate_failed"


class GroqMessagesClient:
    """Drop-in stand-in for `anthropic.AsyncAnthropic` from the calling
    code's point of view — implements only the one method/shape this
    project actually uses, nothing more."""

    class _Messages:
        @staticmethod
        async def _post(body: dict) -> httpx.Response:
            headers = {
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
                return await client.post(_GROQ_ENDPOINT, json=body, headers=headers)

        async def create(
            self, *, model: str = GROQ_MODEL, max_tokens, system, messages, output_config=None, **_ignored
        ):
            # Anthropic shape (system as its own top-level field) ->
            # OpenAI/Groq shape (system folded into the messages array
            # as its own role, first).
            groq_messages = [{"role": "system", "content": system}, *messages]

            body = {
                "model": model,
                "max_tokens": max_tokens,
                "reasoning_effort": _REASONING_EFFORT,
                "messages": groq_messages,
            }
            json_mode = output_config is not None
            # Only requested when the caller asked for structured JSON
            # (decision_detector.py's Tier 2 classification passes
            # output_config; answer_drafter.py's plain-prose drafting
            # call doesn't). Groq's json_object mode requires the word
            # "JSON" to appear somewhere in the prompt, which Tier 2's
            # _SYSTEM_PROMPT already does ("Respond with a single JSON
            # object").
            if json_mode:
                body["response_format"] = {"type": "json_object"}

            response = await self._post(body)

            if json_mode and response.status_code == 400:
                try:
                    error_code = response.json().get("error", {}).get("code")
                except ValueError:
                    error_code = None
                if error_code == _JSON_VALIDATION_RETRY_ERROR_CODE:
                    logger.warning("Groq %s; retrying once", error_code)
                    response = await self._post(body)

            if response.status_code != 200:
                logger.warning(
                    "Groq chat completion returned %s: %s", response.status_code, response.text[:500]
                )
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])

    def __init__(self) -> None:
        self.messages = self._Messages()
