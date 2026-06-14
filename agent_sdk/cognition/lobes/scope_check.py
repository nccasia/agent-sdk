"""scope_check — B4 gate lobe: refuse out-of-domain questions early.

Behavior: runs the policy scope gate (an early refusal seam the embedding
can't filter). Fires iff the policy sets `scope_gate`; the `clarify` path
lists it as a member (no default bias).

Tuning keys: `prior_scope_check` (0), `min_scope_check` (0.5),
`w_scope_gate` (1.0).
Gates: degenerate-parity matrix.
"""

from __future__ import annotations

import logging

from agent_sdk.lobes.runtime import (
    LlmCall,
    Lobe,
    PromptContribution,
    TurnContext,
    extract_text,
)
from agent_sdk.network.activation import LAYER_COGNITION

logger = logging.getLogger(__name__)

# ── Behavior contract ────────────────────────────────────────────────────────
# run(llm, *, query, system_prompt=None, refusal_message=None) -> str | None —
# the refusal message when the query is clearly out of domain, else None.
# FN-safe: anything but an explicit OUT_OF_SCOPE verdict (incl. empty /
# truncated reasoning / provider error) is in-scope — never a false refusal.
# Domain-free: the SDK ships only a GENERIC classifier scaffold. A bot that
# enables scope gating supplies the actual domain via per-bot config
# (`policy.scope_prompt`, `policy.scope_refusal_message`), passed through as
# ``system_prompt`` / ``refusal_message``; the defaults below name no domain and
# only stand in when a host enables the gate without configuring it.

DEFAULT_SYSTEM_PROMPT = """You are a scope classifier for an assistant that only answers questions within its configured domain.
Classify the user's question:
- IN_SCOPE: it is about the assistant's domain.
- OUT_OF_SCOPE: it is unrelated to that domain (off-topic small talk, a different subject, etc.).
Reply with exactly one word: IN_SCOPE or OUT_OF_SCOPE."""

DEFAULT_REFUSAL = (
    "I can only help with questions within my area. Please ask me something in that scope."
)

USER_TEMPLATE = "Query: {query}"


async def run(
    llm: LlmCall,
    *,
    query: str,
    system_prompt: str | None = None,
    refusal_message: str | None = None,
) -> str | None:
    """LLM scope gate: refusal message on an explicit OUT_OF_SCOPE, else None.
    Legacy-exact: max_tokens=256, temperature=0, usage rolled up, fail-open."""
    try:
        resp = await llm(
            stage="simple_answer",
            system=system_prompt or DEFAULT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": USER_TEMPLATE.format(query=query)}],
            max_tokens=256,
            temperature=0,
        )
        verdict = extract_text(resp).upper()
        # Only refuse on an explicit OUT_OF_SCOPE; anything else (incl. empty
        # / truncated reasoning) defaults to in-scope → never a false refusal.
        if "OUT_OF_SCOPE" in verdict and "IN_SCOPE" not in verdict:
            return refusal_message or DEFAULT_REFUSAL
    except Exception:
        logger.exception("scope gate failed")
    return None


class ScopeCheckLobe(Lobe):
    """Policy scope-gate: an LLM check that refuses out-of-scope queries before
    any retrieval/answer work, per the bot's scope policy."""

    id = "scope_check"
    name = "Scope Check"
    description = "Refuse out-of-scope queries up front, per the bot's scope policy."
    use_when = "the bot enables scope gating (`policy.scope_gate`)"
    how = (
        "One LLM call classifies the query against the bot's scope prompt; an "
        "out-of-scope verdict short-circuits the turn with the refusal message, "
        "before retrieval or synthesis. Inert unless `scope_gate` is set."
    )
    system_prompt = DEFAULT_SYSTEM_PROMPT
    user_template = USER_TEMPLATE
    behavior = "gate"
    layer = LAYER_COGNITION
    order = 1
    writes = ("scope_verdict",)
    # Back-compat module-API members.
    DEFAULT_SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT
    DEFAULT_REFUSAL = DEFAULT_REFUSAL
    USER_TEMPLATE = USER_TEMPLATE

    def activation(self, ctx: dict) -> float:
        return 1.0 if ctx.get("scope_gate") else 0.0

    def prompt(self, _ctx: TurnContext) -> list[PromptContribution]:
        return [PromptContribution(DEFAULT_SYSTEM_PROMPT, stability="stable", source=self.id)]

    async def run(
        self,
        llm: LlmCall,
        *,
        query: str,
        system_prompt: str | None = None,
        refusal_message: str | None = None,
        _ctx: TurnContext | None = None,
    ) -> str | None:
        return await run(
            llm,
            query=query,
            system_prompt=system_prompt,
            refusal_message=refusal_message,
        )


LOBE = ScopeCheckLobe()
SPEC = LOBE.spec  # back-compat export
