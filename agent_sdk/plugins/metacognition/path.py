"""Metacognition path (recognition) — when a turn should run the reflect-first ``meta`` flow.

Conservative by construction so it never steals the default qna/research routing: it fires
only on an explicit ask to reconsider the approach, OR when the meta-control tool recorded a
flow bias toward ``meta`` on the previous turn (folded into the recognition ctx as
``meta_flow_bias_meta`` — the deterministic "one more signal" the doc describes; flow is
resolved once at turn start, so a mid-turn bias only takes effect next turn).

**Auto-delegation (opt-in).** :func:`make_recognize` ``(auto_delegate=True)`` adds a deterministic
*complexity* signal so the agent reflects-then-fans-out on genuinely multi-part queries WITHOUT an
explicit "step back" cue (the realistic delegation case — see ``benchmarks/delegationbench``). It
stays a pure function of the query (invariant #4: no LLM judges the pipeline) and is tuned for
precision — it fires on multi-facet / decomposable asks, not on single-fact questions. Off by
default, so the bare metacognition install is unchanged.
"""

from __future__ import annotations

import re

__all__ = ["recognize", "make_recognize", "complexity_score", "META_CUES", "bias_flag"]

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

# Verbs that signal a query wants per-facet treatment (decompose → handle each → combine).
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

# Enumerators that mark explicitly numbered parts ("1)", "(a)", "first", …).
_ENUM_RE = re.compile(r"(?:(?<=\s)|^)(?:\d[).]|\([a-e]\)|first|second|third|fourth|fifth)\b")
# List separators that join independent facets/entities.
_SEP_RE = re.compile(r"\b(?:and|as well as|along with|plus)\b")


def bias_flag(path: str) -> str:
    """The recognition-ctx flag a recorded flow bias toward ``path`` sets (see engine
    ``build_context``). Keeps the resolver a pure function of (spec, context)."""
    return f"meta_flow_bias_{path}"


def complexity_score(query: str) -> float:
    """Deterministic [0,1] estimate that a query is worth decomposing into subagents.

    High when the ask is genuinely multi-facet (a decomposition verb over ≥3 items, ≥3
    enumerated parts, ≥3 questions, or a long list); ~0 for single-fact questions. Tuned for
    precision so auto-delegation does not over-fire — the lever the delegation bench tunes."""
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
    """The ``meta`` flow score in [0,1] (free, deterministic) — conservative cues only."""
    # Next-turn bias the meta_control tool recorded last turn (persisted on the session,
    # folded into ctx by build_context). A deterministic signal, not an LLM judging the flow.
    if ctx.get(bias_flag("meta")):
        return 1.0
    q = str(ctx.get("query") or "").lower()
    return 0.85 if any(c in q for c in META_CUES) else 0.0


def make_recognize(*, auto_delegate: bool = False):
    """Build a ``meta`` recognizer. ``auto_delegate`` adds the complexity signal so the agent
    delegates on complex queries without an explicit cue (off ⇒ identical to :func:`recognize`)."""
    if not auto_delegate:
        return recognize

    def _recognize(ctx: dict) -> float:
        base = recognize(ctx)
        if base > 0.0:
            return base
        return complexity_score(ctx.get("query") or "")

    return _recognize
