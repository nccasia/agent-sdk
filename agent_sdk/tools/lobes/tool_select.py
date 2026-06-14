"""tool_select — adaptively expose only the tools the turn needs.

WHAT   Trims the per-step ``tools=`` payload to the relevant tools, so the model
       isn't shown (and distracted/charged by) the whole toolset.
WHEN   Inert by default; fires only when the bot opts into adaptive exposure
       (``policy.tool_strategy == "adaptive"``). At default weights the lobe is
       dark, so the network is byte-identical at parity.
HOW    See :meth:`ToolSelectLobe.select` — this lobe OWNS its behavior; the
       interpreter just hands it the step's candidate specs and the turn inputs.

This is a control lobe: it produces NO prompt and NO context node. Its single
output is the filtered tool list. The whole algorithm lives in :meth:`select`
below, so this file fully describes what the lobe does — activation AND behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_sdk.lobes.runtime import Lobe
from agent_sdk.network.activation import LAYER_SKILL


class ToolSelectLobe(Lobe):
    """Activation-only control lobe — gates + performs adaptive tool exposure."""

    id = "tool_select"
    name = "Tool Select"
    description = "Expose only the tools relevant to the turn, always keeping essentials."
    use_when = "the turn needs only a few tools, not the whole toolset"
    how = (
        "Score every non-essential candidate tool by relevance of its "
        "`name + description` to the turn query (the shared L1+L2 `score_relevance` "
        "scorer). Keep the essentials unconditionally — the step's explicit "
        "allowlist, kb.*, memory, retrieve_kb, external/unknown MCP tools, and any "
        "tool family the enriched conversation intent marks as needed. Then keep "
        "the highest-scoring non-essentials that clear `tool_min_activation`, up to "
        "`max_tools`; drop the rest (below_floor / max_tools). The kept/dropped "
        "decision is recorded on `trace.tool_selection`. Pure scoring — no LLM call."
    )
    behavior = "select"
    layer = LAYER_SKILL
    order = 2  # after skill_select (0) + skill_active (1) in the SKILL layer
    writes = ()  # control lobe — selection recorded on trace.tool_selection

    def activation(self, ctx: dict) -> float:
        # Inert by default: fires only when the bot opts into adaptive exposure.
        return 1.0 if ctx.get("tool_strategy") == "adaptive" else 0.0

    def select(
        self,
        specs: list[dict],
        *,
        query: str,
        q_vec: Any | None,
        embed_one: Callable | None,
        essential: Callable[[str], bool],
        weights: dict,
        min_activation: float,
        max_tools: int,
    ) -> tuple[list[dict], dict]:
        """The behavior. Returns ``(kept_specs, trace_entry)``.

        ``essential(name) -> bool`` decides the always-kept set (the caller wires
        in the step allowlist + kb.*/memory/external + intent families). Everything
        else is scored by relevance and kept by floor + budget. Deterministic."""
        from agent_sdk.network.context_builder import score_relevance

        essentials = [s for s in specs if essential(str(s.get("name") or ""))]
        scored = []
        for s in specs:
            name = str(s.get("name") or "")
            if essential(name):
                continue
            sc = score_relevance(
                query,
                q_vec,
                f"{name} {s.get('description') or ''}",
                embed_one=embed_one,
                weights=weights,
            )
            scored.append((sc["activation"], sc, s, name))
        scored.sort(key=lambda x: -x[0])

        out = list(essentials)
        kept_meta = [{"name": str(s.get("name") or ""), "essential": True} for s in essentials]
        dropped, non_ess_kept, budget = [], 0, max(0, max_tools - len(essentials))
        for act, sc, s, name in scored:
            meta = {
                "name": name,
                "l1": round(sc["l1"], 3),
                "l2": round(sc["l2"], 3),
                "activation": round(act, 3),
            }
            if act < min_activation:
                dropped.append({**meta, "reason": "below_floor"})
            elif non_ess_kept >= budget:
                dropped.append({**meta, "reason": "max_tools"})
            else:
                out.append(s)
                non_ess_kept += 1
                kept_meta.append({**meta, "essential": False})
        return out, {"kept": kept_meta, "dropped": dropped}


LOBE = ToolSelectLobe()
SPEC = LOBE.spec  # back-compat export
