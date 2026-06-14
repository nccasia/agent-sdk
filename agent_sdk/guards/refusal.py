"""Refusal-rule + golden-answer pre-turn gate — a built-in for the engine's
``pre_turn_gate`` seam.

The engine exposes the *seam* (a ``(query, state) -> AgentResult | None`` run
before any reasoning: non-None ends the turn). This module is the ready
*implementation*: keyword/topic/regex refusal → embed once → golden known-answer
hit (BEFORE semantic refusal, so an approved answer beats a fuzzy guess) →
semantic refusal. Everything is dependency-injected — the host wires its refusal
rules (data), a :class:`GoldenHead`, the query embedder, and an optional
semantic-refusal callable. No host package, ACL, or tenant type enters the leaf;
the gate sees only opaque rows + callables.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from agent_sdk.contracts.memo import Citation
from agent_sdk.result import AgentResult, Refusal

__all__ = ["make_pre_turn_gate", "make_semantic_refusal", "match_refusal", "golden_hit"]


def make_semantic_refusal(
    refusal_rules: Sequence[dict],
    embed: Any,
    threshold: float = 0.72,
    exclude_tags: Sequence[str] = ("not-in-docs",),
):
    """Build ``semantic_refusal(q_vec) -> (reason | None, top_cosine, matched_examples, matched_tags)``
    from ``rule_type='semantic'`` rules (each carrying ``query_examples`` that
    paraphrase the kind of out-of-scope/disallowed ask to refuse).

    Embeds each example once at build time (cosine over the L2-normalized matrix).
    The reason of the NEAREST example above ``threshold`` wins. Returns None when
    there are no usable semantic rules. ``exclude_tags`` drops topic-relevant-but-
    absent rules (e.g. ``not-in-docs``) that weak embeddings can't separate from
    answerable in-domain questions — leave those to ground-or-refuse at synthesis.
    """
    import numpy as np

    excluded = {str(t) for t in (exclude_tags or ())}
    rows: list[tuple[str, str, tuple[str, ...]]] = []
    for rule in refusal_rules or ():
        if str(rule.get("rule_type")) != "semantic":
            continue
        tags = tuple(str(t) for t in (rule.get("tags") or ()))
        if excluded & set(tags):
            continue
        reason = rule.get("reason") or "This question is outside what I can help with."
        examples = rule.get("query_examples") or (
            [rule["query_example"]] if rule.get("query_example") else []
        )
        for ex in examples:
            ex = str(ex or "").strip()
            if ex:
                rows.append((ex, reason, tags))
    if not rows or embed is None:
        return None
    try:
        vecs = np.asarray([np.asarray(embed(t), dtype=np.float32) for t, _, _ in rows])
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vecs = vecs / norms
    except Exception:
        return None
    reasons = [r for _, r, _ in rows]
    tags_per = [t for _, _, t in rows]
    # All example texts grouped by reason — so a caller's lexical (bigram) veto can
    # check the query against EVERY phrasing of the matched rule, not just the
    # single cosine-nearest one.
    examples_by_reason: dict[str, tuple[str, ...]] = {}
    for e, r, _ in rows:
        examples_by_reason.setdefault(r, ())
        examples_by_reason[r] = examples_by_reason[r] + (e,)

    def semantic_refusal(q_vec: Any) -> tuple[str | None, float, tuple[str, ...], tuple[str, ...]]:
        """``(reason, top_cosine, matched_examples, matched_tags)`` — reason is the
        nearest refusal example's reason when its cosine ≥ threshold, else None.
        ``top_cosine`` lets a caller margin-compare against KB similarity;
        ``matched_examples`` is every phrasing of the matched rule (for a lexical
        veto); ``matched_tags`` lets a caller route HARD refusals past a retrieval gate."""
        try:
            qv = np.asarray(q_vec, dtype=np.float32)
            n = float(np.linalg.norm(qv))
            if n > 0:
                qv = qv / n
            sims = vecs @ qv
            idx = int(sims.argmax())
            score = float(sims[idx])
        except Exception:
            return None, 0.0, (), ()
        if score < threshold:
            return None, score, (), ()
        return reasons[idx], score, examples_by_reason.get(reasons[idx], ()), tags_per[idx]

    return semantic_refusal


def _refusal(reason: str) -> AgentResult:
    return AgentResult(
        text=reason,
        status="refused",
        refusal=Refusal(reason="policy_violation", message=reason),
    )


def match_refusal(query: str, rules: Sequence[dict]) -> str | None:
    """First matching refusal rule's reason, else None. Rule:
    ``{rule_type: keyword|topic|regex, pattern, reason}`` (keyword supports
    pipe-separated alternatives)."""
    low = query.lower()
    for rule in rules or ():
        pattern = rule.get("pattern", "")
        if not pattern:
            continue
        reason = rule.get("reason", "This topic is restricted.")
        rtype = rule.get("rule_type", "keyword")
        if rtype == "keyword":
            if any(kw.strip() and kw.strip() in low for kw in pattern.lower().split("|")):
                return reason
        elif rtype == "topic":
            if pattern.lower() in low:
                return reason
        else:  # regex
            try:
                if re.search(pattern, query, re.IGNORECASE):
                    return reason
            except re.error:
                continue
    return None


def golden_hit(q_vec: Any, head: Any, threshold: float) -> AgentResult | None:
    """A near-duplicate of an approved golden question returns its answer directly,
    cited ``golden://<case_id>`` (cosine over the L2-normalized golden head)."""
    if head is None or getattr(head, "embeddings", None) is None or not getattr(head, "items", None):
        return None
    try:
        sims = head.embeddings @ q_vec
        idx = int(sims.argmax())
        score = float(sims[idx])
    except Exception:
        return None
    if score < threshold:
        return None
    item = head.items[idx]
    answer = (getattr(item, "expected_behavior", "") or "").strip()
    if not answer:
        return None
    return AgentResult(
        text=answer,
        status="answered",
        citations=[
            Citation(
                chunk_id=f"golden:{item.case_id}",
                source_ref=f"golden://{item.case_id}",
                supporting_span=(0, len(answer)),
            )
        ],
    )


def make_pre_turn_gate(
    *,
    refusal_rules: Sequence[dict] = (),
    golden_head: Any = None,
    embed: Callable[[str], Any] | None = None,
    golden_threshold: float = 0.86,
    semantic_refusal: Callable[[Any], Awaitable[str | None] | str | None] | None = None,
    refusal_enforcement: str = "hard",
):
    """Build the ``pre_turn_gate`` callable. Order: keyword/topic/regex refusal →
    embed once → golden BEFORE semantic refusal (an approved answer must win over a
    fuzzy semantic-refusal guess) → semantic refusal. ``refusal_enforcement="disabled"``
    makes the gate a no-op."""

    async def gate(query: str, state: Any) -> AgentResult | None:
        if refusal_enforcement == "disabled":
            return None
        reason = match_refusal(query, refusal_rules)
        if reason:
            return _refusal(reason)
        if embed is None:
            return None
        try:
            q_vec = embed(query)
        except Exception:
            return None
        hit = golden_hit(q_vec, golden_head, golden_threshold)
        if hit is not None:
            return hit
        if semantic_refusal is not None:
            sem = semantic_refusal(q_vec)
            if hasattr(sem, "__await__"):
                sem = await sem
            # ``make_semantic_refusal`` returns a tuple; tolerate a bare-reason callable too.
            reason = sem[0] if isinstance(sem, tuple) else sem
            if reason:
                return _refusal(reason)
        return None

    return gate
