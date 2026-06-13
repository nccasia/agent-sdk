"""classify — B4 route lobe: the LLM router, with a deterministic skip.

Behavior: routes simple/complex (one provider call) and writes a `route`
node. Excites `plan` and `synthesize` at weight 1.0 (the degenerate chain).

classify-skip (RFC 0015 "the first earned win", shipped ON since ENGINE
0.4.0): `is_simple_shape` inhibits the router on high-confidence simple
queries via `w_simple_shape = -0.6` — one provider round-trip saved per such
turn (the measured p95 lever). Per-bot opt-out:
`flow_lobe_weights["w_simple_shape"] = 0`.

Tuning keys: `prior_classify` (0), `min_classify` (0.5),
`w_has_stage_classify` (1.0), `w_simple_shape` (-0.6),
`edge_classify__plan` / `edge_classify__synthesize` (1.0),
`path_research__classify` (0.2).
Gates: degenerate-parity matrix (classify-skip is the documented intentional
delta); attentionbench `efficiency` / `compare`.
"""

from __future__ import annotations

from typing import Literal

from agent_sdk.lobes.paths.qna import recognize as _qna_score
from agent_sdk.lobes.patterns import ANAPHORA_RE, FIRED_PROMPT_RE
from agent_sdk.lobes.runtime import (
    BaseLobe,
    LlmCall,
    PromptContribution,
    TurnContext,
    extract_text,
)
from agent_sdk.network.activation import LAYER_COGNITION, LobeSpec

# ── Behavior contract ────────────────────────────────────────────────────────
# run(llm, *, query) -> "simple" | "complex" — one cheap routing call; any
# unparseable output defaults to "simple" (degrade, never cascade). The
# interpreter's classify-exception fallback (route="simple", lobe failed)
# wraps THIS call.

SYSTEM_PROMPT = """You are a query complexity classifier. Classify the user query as either "simple" or "complex".

Rules:
- "simple": A single knowledge lookup can answer the question. Factual questions, definitional queries, direct KB lookups.
- "complex": Requires multi-step reasoning, comparing information from multiple sources, investigating multiple aspects, or synthesis of several pieces of information.

Respond with ONLY the word "simple" or "complex"."""

USER_TEMPLATE = "Query: {query}"


async def run(llm: LlmCall, *, query: str) -> Literal["simple", "complex"]:
    """Route the turn. Legacy-exact: max_tokens=10, no usage roll-up."""
    response = await llm(
        stage="classify",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": USER_TEMPLATE.format(query=query)}],
        max_tokens=10,
        count_usage=False,
    )
    t = extract_text(response).strip().lower()
    return "complex" if t.startswith("complex") else "simple"


def is_simple_shape(ctx: dict) -> bool:
    """classify-skip (RFC 0015 'the first earned win'): a high-confidence
    SIMPLE query needs no LLM router — the
    simple graph runs directly, saving one provider round-trip per such turn
    (the measured p95 lever). Conservative on purpose: only a strongly
    qna-shaped query (short, interrogative, zero breadth/task/anaphora cues)
    qualifies; anything ambiguous still pays for the router."""
    query = str(ctx.get("query") or "")
    if ctx.get("fired_prompt") or FIRED_PROMPT_RE.search(query):
        return False
    if _qna_score(ctx) < 0.8:  # interrogative AND short, with every excluder
        return False
    # referents may hide multi-hop work
    return not ANAPHORA_RE.search(query)


def signals(ctx: dict) -> dict[str, float]:
    stages = set(ctx.get("stages") or ())
    return {
        "has_stage_classify": 1.0 if "classify" in stages else 0.0,
        # Inhibitory: high-confidence simple shape drops the router below
        # threshold (w_simple_shape < 0) — deterministic, free, reversible
        # per-bot via flow_lobe_weights["w_simple_shape"] = 0.
        "simple_shape": 1.0 if is_simple_shape(ctx) else 0.0,
    }


SPEC = LobeSpec(
    id="classify",
    behavior="route",
    layer=LAYER_COGNITION,
    order=2,
    prior=0.0,
    signals=signals,
    edges={"plan": 1.0, "synthesize": 1.0},
    writes=("route",),
)


class ClassifyLobe(BaseLobe):
    """Executable query-complexity routing lobe."""

    spec = SPEC
    SYSTEM_PROMPT = SYSTEM_PROMPT
    USER_TEMPLATE = USER_TEMPLATE

    def signals(self, ctx: dict) -> dict[str, float]:
        return signals(ctx)

    def prompt(self, _ctx: TurnContext) -> list[PromptContribution]:
        return [PromptContribution(SYSTEM_PROMPT, stability="stable", source=self.id)]

    async def run(
        self, llm: LlmCall, *, query: str, _ctx: TurnContext | None = None
    ) -> Literal["simple", "complex"]:
        return await run(llm, query=query)

    def is_simple_shape(self, ctx: dict) -> bool:
        return is_simple_shape(ctx)


LOBE = ClassifyLobe()
