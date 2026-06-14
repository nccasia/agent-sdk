"""Task path (recognition) — when a turn is a task to drive.

A scheduled-execution fire (``fired_prompt``) or an analytical / multi-step request.
Kept distinct from a plain wh-question (which stays ``qna``); scored above ``qna`` so an
analytical ask routes here. Tunable in one place — the cue set + score.
"""

from __future__ import annotations

__all__ = ["recognize", "TASK_CUES"]

# Analytical / action cues — "a task to accomplish", not a plain question.
TASK_CUES = (
    "compute",
    "calculate",
    "find ",
    "identify",
    "list ",
    "report",
    "run ",
    "total",
    "how many",
    "top ",
    "average",
    "per ",
    "summar",
    "analyz",
    "rank",
    "breakdown",
)


def recognize(ctx: dict) -> float:
    """The ``task`` path score in [0,1] (free, deterministic)."""
    if ctx.get("fired_prompt"):
        return 1.0
    q = str(ctx.get("query") or "").lower()
    return 0.9 if any(c in q for c in TASK_CUES) else 0.0  # beat qna (≈0.8) on analytical asks
