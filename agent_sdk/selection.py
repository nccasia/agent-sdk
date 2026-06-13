"""The unified selection-hint API — optimize context by tier.

Every candidate the engine could put in the prompt — a **tool**, a **skill**, or a
context node — competes for the window. Rather than dump them all, the engine
routes each to one of three exposure tiers (PreAct, ``react-context-management``):

- ``inject`` — full content in the prompt (the model sees it now).
- ``hint``   — a one-line menu entry; the item stays callable/fetchable on demand
  (a tool kept in the runtime, a skill behind ``ActivateSkill``), so it is
  discoverable without spending its full token cost.
- ``drop``   — not surfaced this turn.

``select_with_hints`` is the shared mechanism for tools and skills; context nodes
use the node-shaped ``build_attention`` + ``route_tiers`` directly. It reuses the
same relevance scorer (``score_relevance``) and the CDS/tier thresholds from
``DEFAULT_NODE_WEIGHTS`` so tool/skill/context selection behave consistently.

Defaults reproduce the legacy "expose everything" behavior: with no budget and a
zero inject threshold, every candidate is ``inject``. Tightening engages only when
the caller passes a budget and/or thresholds.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from agent_sdk.network.context_builder import DEFAULT_NODE_WEIGHTS, score_relevance
from agent_sdk.skills import est_tokens

__all__ = ["Tier", "Selected", "select_with_hints"]

Tier = Literal["inject", "hint", "drop"]


@dataclass
class Selected:
    item: Any
    key: str
    tier: Tier
    score: float
    hint: str  # one-line menu entry (used when tier == "hint")

    def to_json(self) -> dict:
        return {"key": self.key, "tier": self.tier, "score": round(self.score, 4)}


def select_with_hints(
    items: Sequence[Any],
    query: str,
    q_vec: Any | None = None,
    *,
    key: Callable[[Any], str],
    text: Callable[[Any], str],
    hint: Callable[[Any], str] | None = None,
    weights: dict[str, float] | None = None,
    inject_threshold: float = 0.0,
    hint_threshold: float = 0.0,
    budget_tokens: int | None = None,
    essentials: Sequence[str] = (),
    min_keep: int = 3,
    embed_one: Callable | None = None,
) -> list[Selected]:
    """Route ``items`` to ``inject`` / ``hint`` / ``drop`` tiers, optimizing context.

    ``key(item)`` → stable id (matched against ``essentials``). ``text(item)`` →
    the text scored for relevance. ``hint(item)`` → the one-line menu entry
    (defaults to ``text``). Essentials always inject. With the defaults
    (``inject_threshold=0`` and no ``budget_tokens``) every item injects — the
    legacy behavior; pass the thresholds (e.g. from ``DEFAULT_NODE_WEIGHTS``
    ``tier_inject_threshold``/``tier_hint_threshold``) and a budget to engage
    adaptive trimming.
    """
    w = weights or DEFAULT_NODE_WEIGHTS
    hint = hint or text
    ess = set(essentials)

    scored: list[tuple[Any, str, float, int]] = []  # (item, key, score, tokens)
    for item in items:
        k = key(item)
        sc = score_relevance(query or "", q_vec, text(item), embed_one=embed_one, weights=w)
        scored.append((item, k, float(sc["activation"]), est_tokens(text(item))))

    # Essentials first, then by score — so the budget fills with the most relevant.
    order = sorted(scored, key=lambda r: (r[1] not in ess, -r[2]))

    out: list[Selected] = []
    injected_tokens = 0
    kept_count = 0
    for item, k, score, tokens in order:
        is_ess = k in ess
        over_budget = budget_tokens is not None and injected_tokens + tokens > budget_tokens
        if is_ess or (score >= inject_threshold and not over_budget):
            tier: Tier = "inject"
            injected_tokens += tokens
        elif score >= hint_threshold or kept_count < min_keep:
            tier = "hint"
        else:
            tier = "drop"
        if tier != "drop":
            kept_count += 1
        out.append(Selected(item=item, key=k, tier=tier, score=score, hint=hint(item)))
    # Return in the original item order (stable, cache-friendly), tiers attached.
    by_key = {s.key: s for s in out}
    return [by_key[key(item)] for item in items]
