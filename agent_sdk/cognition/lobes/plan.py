"""plan — B4 decompose lobe: the complex-path entry point.

Behavior: decomposes a complex query into aspects (`aspect` nodes) and
excites `research`. Dispatch hangs off this lobe: the complex graph runs iff
`plan` activates in the post-classify resolution.

Threshold 1.5 encodes a CONJUNCTION: the classify edge (1.0 · a_classify)
AND route="complex" (1.0) are both required — exactly the legacy
"classify completed and said complex" predicate.

Tuning keys: `prior_plan` (0), `min_plan` (1.5), `w_route_complex` (1.0),
`edge_classify__plan` (1.0), `edge_plan__research` (1.0),
`path_research__plan` (0.2).
Gates: degenerate-parity matrix (the complex-path arm); attentionbench `diff`.
"""

from __future__ import annotations

import json

from agent_sdk.lobes.runtime import (
    BaseLobe,
    LlmCall,
    PromptContribution,
    TurnContext,
    extract_text,
)
from agent_sdk.network.activation import LAYER_COGNITION, LobeSpec
from agent_sdk.network.context_builder import ContextNode

# ── Behavior contract ────────────────────────────────────────────────────────
# run(llm, *, query) -> list[aspect] — decompose into 2-5 research aspects.
# An unparseable reply degrades to the single-aspect fallback
# [{"id": "main", "question": query}]; a PROVIDER error propagates (legacy —
# the complex path fails loudly rather than researching a guessed plan).
# nodes(aspects) -> the `aspect` write-back nodes (blackboard).

SYSTEM_PROMPT = """You are a research planning agent. Given the user's query, break it down into 2-5 distinct research aspects.
Each aspect should be a self-contained sub-question that can be investigated independently.

Respond with a JSON object with an "aspects" key, each aspect having:
- "id": a short slug
- "question": the specific question to investigate

Example:
{{"aspects": [{{"id": "policy_details", "question": "What are the specific PTO accrual rates?"}}]}}
"""

USER_TEMPLATE = "Query: {query}"


async def run(llm: LlmCall, *, query: str) -> list[dict]:
    """Decompose the query. Legacy-exact: max_tokens=512, no usage roll-up,
    provider errors propagate, only parse failures degrade."""
    response = await llm(
        stage="plan",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": USER_TEMPLATE.format(query=query)}],
        max_tokens=512,
        count_usage=False,
    )
    try:
        return json.loads(extract_text(response)).get("aspects", [])
    except Exception:
        return [{"id": "main", "question": query}]


def nodes(plan_entries: list) -> list[ContextNode]:
    """`aspect` write-back nodes from the traced plan entries."""
    return [
        ContextNode(id=f"aspect:{i}", kind="aspect", text=str(a.get("aspect") or a)[:200])
        for i, a in enumerate(plan_entries)
    ]


def signals(ctx: dict) -> dict[str, float]:
    return {"route_complex": 1.0 if ctx.get("route") == "complex" else 0.0}


SPEC = LobeSpec(
    id="plan",
    behavior="decompose",
    layer=LAYER_COGNITION,
    order=3,
    prior=0.0,
    signals=signals,
    # Threshold 1.5: needs BOTH the classify edge (1.0) and
    # route="complex" (1.0) — the legacy complex-path predicate.
    min_activation=1.5,
    edges={"research": 1.0},
    writes=("aspect",),
)


class PlanLobe(BaseLobe):
    """Executable research-planning lobe."""

    spec = SPEC
    SYSTEM_PROMPT = SYSTEM_PROMPT
    USER_TEMPLATE = USER_TEMPLATE

    def signals(self, ctx: dict) -> dict[str, float]:
        return signals(ctx)

    def prompt(self, _ctx: TurnContext) -> list[PromptContribution]:
        return [PromptContribution(SYSTEM_PROMPT, stability="stable", source=self.id)]

    async def run(self, llm: LlmCall, *, query: str, _ctx: TurnContext | None = None) -> list[dict]:
        return await run(llm, query=query)

    def nodes(self, plan_entries: list, *, _ctx: TurnContext | None = None) -> list[ContextNode]:
        return nodes(plan_entries)


LOBE = PlanLobe()
