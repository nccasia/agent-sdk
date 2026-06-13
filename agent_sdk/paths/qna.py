"""qna — short factual question, answered by the simple graph.

Score: interrogative +0.6 · 1–14 words +0.2.
Excluders (→ 0.0): comparative/breadth, fired prompt, reminder/mutation verb,
bare greeting (greeting without an interrogative).

Members/bias: synthesize +0.1 (cite/filter members carry no bias — pinned).
Tuning keys: `path_qna__synthesize` (0.1).
Gates: attentionbench `paths` fixtures; `is_simple_shape` consumes this score
(classify-skip requires ≥ 0.8 — interrogative AND short with every excluder).
"""

from __future__ import annotations

from agent_sdk.lobes.patterns import (
    COMPARATIVE_RE,
    FIRED_PROMPT_RE,
    GREETING_RE,
    INFO_REQUEST_RE,
    INTERROGATIVE_RE,
    MUTATION_RE,
    REMINDER_RE,
    _word_count,
)
from agent_sdk.network.activation import PathSpec


def recognize(ctx: dict) -> float:
    query = str(ctx.get("query") or "")
    if COMPARATIVE_RE.search(query) or FIRED_PROMPT_RE.search(query):
        return 0.0
    if REMINDER_RE.search(query) or MUTATION_RE.search(query):
        return 0.0
    score = 0.0
    # A knowledge query is either an interrogative OR a declarative information
    # request ("tôi cần hướng dẫn …") — both need KB grounding and route to the
    # simple graph (which prefetches grounding). Without the latter, declarative
    # requests fall to the no-retrieval emergent path and answer ungrounded.
    if INTERROGATIVE_RE.search(query) or INFO_REQUEST_RE.search(query):
        score += 0.6
    if 0 < _word_count(query) <= 14:
        score += 0.2
    if GREETING_RE.search(query) and not INTERROGATIVE_RE.search(query):
        return 0.0
    return score


PATH = PathSpec(
    name="qna",
    recognizer=recognize,
    members=("synthesize", "cite", "filter"),
    bias={"synthesize": 0.1},
)
