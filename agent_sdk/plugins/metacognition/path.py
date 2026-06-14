"""Metacognition path (recognition) — when a turn should run the reflect-first ``meta`` flow.

Conservative by construction so it never steals the default qna/research routing: it fires
only on an explicit ask to reconsider the approach, OR when the meta-control tool recorded a
flow bias toward ``meta`` on the previous turn (folded into the recognition ctx as
``meta_flow_bias_meta`` — the deterministic "one more signal" the doc describes; flow is
resolved once at turn start, so a mid-turn bias only takes effect next turn).
"""

from __future__ import annotations

__all__ = ["recognize", "META_CUES", "bias_flag"]

# Explicit "reconsider how you're approaching this" cues — NOT a plain task/question.
META_CUES = (
    "rethink",
    "reconsider",
    "step back",
    "different approach",
    "your approach",
    "think about how",
    "plan your approach",
    "break this down differently",
)


def bias_flag(path: str) -> str:
    """The recognition-ctx flag a recorded flow bias toward ``path`` sets (see engine
    ``build_context``). Keeps the resolver a pure function of (spec, context)."""
    return f"meta_flow_bias_{path}"


def recognize(ctx: dict) -> float:
    """The ``meta`` flow score in [0,1] (free, deterministic)."""
    # Next-turn bias the meta_control tool recorded last turn (persisted on the session,
    # folded into ctx by build_context). A deterministic signal, not an LLM judging the flow.
    if ctx.get(bias_flag("meta")):
        return 1.0
    q = str(ctx.get("query") or "").lower()
    return 0.85 if any(c in q for c in META_CUES) else 0.0
