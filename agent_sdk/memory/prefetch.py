"""Always-on memory prefetch — the turn-start ``## Memory`` index.

The durable ``memory`` tool lets the model *recall on demand*, but the highest-
density recall is the one the model never has to ask for: a scope-ordered index
of the relevant facts, already in the prompt. This hook loads the store at turn
start into ``TurnContext.memory_items`` so the ``memory_recall`` lobe renders
those facts as context nodes — most recalls then cost zero tool calls
(``docs/concepts/context-memory.md``).

Scope order is broad → specific (``bot → user → channel → conversation``) so the
attention builder's per-scope boosts let the most specific fact win on conflict.
Values are inlined under a char budget; entries past the budget degrade to a
one-line *hint* ("recall to read") instead of flooding the prompt — exactly the
Tier-1-inject / Tier-2-hint split the context tiers apply downstream.

No-op by construction: an empty store (or a failing one — ``_prefetch`` isolates
exceptions) yields ``{"memory_items": ()}`` → no nodes → byte-identical prompt.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

__all__ = ["memory_prefetch_hook", "SCOPE_ORDER"]

# Broad → specific. The attention builder boosts conversation > channel > user >
# bot, so the most specific fact still wins on conflict regardless of this order;
# this only sets the rendered node order.
SCOPE_ORDER = ("bot", "user", "channel", "conversation")


def _ordered_scopes(scopes: Sequence[str]) -> list[str]:
    known = [s for s in SCOPE_ORDER if s in scopes]
    rest = [s for s in scopes if s not in SCOPE_ORDER]
    return known + rest


def memory_prefetch_hook(
    memory: Any,
    *,
    scopes: Sequence[str] | None = None,
    k: int = 5,
    value_budget_chars: int = 1200,
) -> Callable[[str, Any], Any]:
    """Build the always-on memory prefetch hook for ``memory`` (a ``Memory``).

    Returns an async ``hook(query, state) -> {"memory_items": [...]}`` that
    searches each scope for the turn's ``query`` and renders the hits into the
    ``[{scope, key, value, description?}]`` shape the ``memory_recall`` lobe
    consumes. Over-budget values are cleared and flagged "recall to read" so they
    surface as Tier-2 hints, not Tier-1 bodies.
    """
    scope_list = _ordered_scopes(tuple(scopes) if scopes else tuple(memory.scopes))

    async def hook(query: str, _state: Any) -> dict:
        items: list[dict] = []
        for scope in scope_list:
            try:
                found = await memory.search(scope, query, k)
            except Exception:
                continue
            for it in found:
                items.append({
                    "scope": getattr(it, "scope", scope),
                    "key": getattr(it, "key", ""),
                    "value": getattr(it, "value", ""),
                })
        # Inline values under the budget; degrade the overflow to hint-only so a
        # long fact is discoverable (Tier 2) without flooding the prompt.
        used = 0
        for d in items:
            v = "" if d["value"] is None else str(d["value"])
            if used + len(v) > value_budget_chars:
                d["description"] = f"{d['key']} (recall to read)"
                d["value"] = ""
            else:
                used += len(v)
        return {"memory_items": items}

    return hook
