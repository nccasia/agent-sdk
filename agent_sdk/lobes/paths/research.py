"""research — multi-part/comparative question, the complex graph.

Score: comparative/breadth +0.7 · ≥15 words +0.3.
Excluders (→ 0.0): fired prompt; reminder/mutation verb — a research-shaped
payload under a scheduling/mutation verb is a task turn ("lên lịch tổng hợp
tin tức mỗi tối…"): intent dominates shape.

Members/bias: classify +0.2, plan +0.2, research +0.2.
Tuning keys: `path_research__classify` / `__plan` / `__research` (0.2).
Gates: attentionbench `paths` fixtures; biases must never cross plan's 1.5
conjunction on their own (parity matrix).
"""

from __future__ import annotations

from agent_sdk.lobes.patterns import (
    COMPARATIVE_RE,
    FIRED_PROMPT_RE,
    MUTATION_RE,
    REMINDER_RE,
    _word_count,
    is_recurring_schedule,
)
from agent_sdk.network.activation import PathSpec


def recognize(ctx: dict) -> float:
    query = str(ctx.get("query") or "")
    if FIRED_PROMPT_RE.search(query):
        return 0.0
    # A research-shaped payload under a scheduling/mutation verb — or under a
    # recurring cadence+clock frame ("tổng hợp tin tức mỗi tối lúc 21h") — is a
    # task turn: intent dominates shape.
    if REMINDER_RE.search(query) or MUTATION_RE.search(query) or is_recurring_schedule(query):
        return 0.0
    score = 0.0
    if COMPARATIVE_RE.search(query):
        score += 0.7
    if _word_count(query) >= 15:
        score += 0.3
    return score


PATH = PathSpec(
    name="research",
    recognizer=recognize,
    members=("classify", "plan", "research", "synthesize", "cite", "filter"),
    bias={"classify": 0.2, "plan": 0.2, "research": 0.2},
)
