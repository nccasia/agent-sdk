"""clarify — anaphoric follow-up inside an information thread.

Score: anaphora +0.5 · ≤8 words +0.3 · interrogative +0.2 · `prev_path`
info-ish +0.15 (conversation continuity), capped 1.0; ZEROED below 0.5 (a
weak clarify shape stays dark rather than nudging members).
Excluders (→ 0.0): no session history, reminder/mutation verb, fired prompt,
soft-cancel (an abandonment is not a clarification request).

Members/bias: condense +0.25 (scope_check/synthesize members carry no bias).
Tuning keys: `path_clarify__condense` (0.25).
Gates: attentionbench `paths` / `pathwalk` (info-thread continuity margin).
"""

from __future__ import annotations

from agent_sdk.lobes.patterns import (
    ANAPHORA_RE,
    FIRED_PROMPT_RE,
    INTERROGATIVE_RE,
    MUTATION_RE,
    REMINDER_RE,
    SOFT_CANCEL_RE,
    _word_count,
)
from agent_sdk.network.activation import PathSpec
from agent_sdk.paths.common import INFOISH_PATHS


def recognize(ctx: dict) -> float:
    query = str(ctx.get("query") or "")
    if not ctx.get("has_history"):
        return 0.0
    if REMINDER_RE.search(query) or MUTATION_RE.search(query) or FIRED_PROMPT_RE.search(query):
        return 0.0
    if SOFT_CANCEL_RE.search(query):
        return 0.0  # an abandonment is not a clarification request
    score = 0.0
    if ANAPHORA_RE.search(query):
        score += 0.5
    if _word_count(query) <= 8:
        score += 0.3
    if INTERROGATIVE_RE.search(query):
        score += 0.2
    # Conversation continuity: a follow-up inside an information thread
    # (prev turn qna/research/clarify) clarifies with a higher margin.
    if ctx.get("prev_path") in INFOISH_PATHS:
        score += 0.15
    return min(1.0, score) if score >= 0.5 else 0.0


PATH = PathSpec(
    name="clarify",
    recognizer=recognize,
    members=("condense", "scope_check", "synthesize"),
    bias={"condense": 0.25},
    grounds=False,  # asks the user to clarify — no KB answer to cite
)
