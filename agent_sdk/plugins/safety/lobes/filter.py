"""filter — B5 verify lobe: the ground-or-refuse contract (grounding output-contract lobe).

Behavior: enforces `refuse_if: no_citations` — refuses rather than ship an
ungrounded claim (`refusal_verdict` node). One of the `OUTPUT_CONTRACT_LOBES`
(agent_sdk.network.activation): activation is path-grounds-gated (live on qna/research,
dark elsewhere) and weight-immune via the gate; see cite.py. The runtime
ground-or-refuse SAFETY contract is NOT this lobe's activation — it is enforced
in the interpreter (`enforce_citations`, keyed on whether retrieval ran),
independent of the lobe network.

Tuning keys: none that change activation (path-gated). `budget_filter` (1600).
Gates: grounding-path activation test; refusal-correctness CI gates.
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
from agent_sdk.network.activation import LAYER_EXPRESSION, LobeSpec

# ── Behavior contract ────────────────────────────────────────────────────────
# run(llm, *, answer) -> (output, refused, reason) — the complex-path LLM
# filter pass (PII redaction / unsupported-claim removal). Unparseable output
# passes the answer through unfiltered (refused=False). The DETERMINISTIC
# no-citations refusal happens BEFORE this call, in the orchestration — a
# weight or prompt can never disable it.

SYSTEM_PROMPT = """You are an output filter. Apply the following rules:
1. REFUSE (return refusal_reason="no_citations") if no verified citations are present
2. Redact any PII (emails, phone numbers, SSNs)
3. Remove speculation, opinions, or information not supported by citations

Respond with:
- "output": the filtered markdown answer
- "refuse": true/false
- "refusal_reason": reason if refused"""

USER_TEMPLATE = "Answer to filter:\n{answer}"

# Flow-axis gate pass (the "filter" FlowStep, _run_pipeline). Its text IS the
# pipeline's FINAL response — it must ship the grounded answer or a refusal,
# never an analysis or a JSON verdict.
FLOW_GATE_PROMPT = """You are the ground-or-refuse gate of a research pipeline.
The system context carries the grounded answer (under "## Step output — cite", falling
back to "## Step output — synthesize") and the evidence index of chunks actually read.

Output the FINAL user-facing message, in the user's language:
- If the answer's claims are supported by the evidence index, output the answer
  UNCHANGED (keep its inline [chunk_id] citations). Redact any PII.
- If the evidence index is missing or supports none of the claims, output a short
  refusal explaining you could not ground the answer in the knowledge base.
Output ONLY the final message — no analysis, no verdict, no JSON."""


async def run(llm: LlmCall, *, answer: str) -> tuple[str, bool, str | None]:
    """The LLM filter pass. Legacy-exact: max_tokens=1024, temperature=0,
    no usage roll-up, pass-through on unparseable output."""
    msg = await llm(
        stage="filter",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": USER_TEMPLATE.format(answer=answer)}],
        max_tokens=1024,
        temperature=0.0,
        count_usage=False,
    )
    try:
        data = json.loads(extract_text(msg))
        if data.get("refuse"):
            return extract_text(msg), True, data.get("refusal_reason", "no_citations")
        return data.get("output", answer), False, None
    except Exception:
        return answer, False, None


def signals(_ctx: dict) -> dict[str, float]:
    # Activation is driven by the resolved path's grounding flag (the
    # OUTPUT_CONTRACT_LOBES gate in propagate()), not by signals.
    return {}


SPEC = LobeSpec(
    id="filter",
    behavior="verify",
    layer=LAYER_EXPRESSION,
    order=1,
    prior=0.0,  # activation is path-grounds-gated, not prior-driven
    pinned=False,  # grounding output-contract lobe — see OUTPUT_CONTRACT_LOBES
    signals=signals,
    writes=("refusal_verdict",),
)


class FilterLobe(BaseLobe):
    """Executable grounded-output filtering lobe."""

    spec = SPEC
    SYSTEM_PROMPT = SYSTEM_PROMPT
    USER_TEMPLATE = USER_TEMPLATE

    def signals(self, ctx: dict) -> dict[str, float]:
        return signals(ctx)

    def prompt(self, _ctx: TurnContext) -> list[PromptContribution]:
        return [PromptContribution(SYSTEM_PROMPT, stability="stable", source=self.id)]

    async def run(
        self, llm: LlmCall, *, answer: str, _ctx: TurnContext | None = None
    ) -> tuple[str, bool, str | None]:
        return await run(llm, answer=answer)


LOBE = FilterLobe()
