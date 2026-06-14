"""Planning path (recognition) — when a turn should run the plan-first ReAct flow.

The "identify a complex problem" capacity, as a deterministic free signal: ``complexity_score``
fires on genuinely multi-faceted / decomposable queries (a decomposition verb over several items,
≥3 enumerated parts, ≥3 questions, or a long list) and stays ~0 on single-fact questions. Pure
function of the query — no LLM judges the pipeline (invariant #4). When it fires, the agent runs a
ReAct loop that plans its steps with ``TodoWrite`` and works them itself (no fan-out).
"""

from __future__ import annotations

import re

__all__ = ["recognize", "complexity_score"]

_DELEGATE_VERBS = (
    "compare",
    "contrast",
    "evaluate",
    "assess",
    "analyze",
    "analyse",
    "research",
    "investigate",
    "audit",
    "review each",
    "summarize each",
    "summarise each",
    "for each",
    "each of",
    "respectively",
    "pros and cons",
    "across these",
    "break down",
    "breakdown",
)
_ENUM_RE = re.compile(r"\(\d\)|(?:(?<=\s)|^)(?:\d[).]|\([a-e]\)|first|second|third|fourth|fifth)\b")
_SEP_RE = re.compile(r"\b(?:and|as well as|along with|plus)\b")


def complexity_score(query: str) -> float:
    """Deterministic [0,1] estimate that a query is worth planning into multiple steps.

    High when the ask is genuinely multi-facet (a decomposition verb over ≥3 items, ≥3 enumerated
    parts, ≥3 questions, or a long list); ~0 for single-fact questions. Tuned for precision."""
    q = str(query or "").lower()
    if not q:
        return 0.0
    verb = any(v in q for v in _DELEGATE_VERBS)
    seps = q.count(", ") + len(_SEP_RE.findall(q))
    enum = len(_ENUM_RE.findall(q))
    questions = q.count("?")
    if enum >= 3 or questions >= 3 or seps >= 4 or (verb and seps >= 3):
        return 0.85
    return 0.0


def recognize(ctx: dict) -> float:
    """The ``plan`` flow score in [0,1] — fire on complex, multi-step queries."""
    return complexity_score(ctx.get("query") or "")
