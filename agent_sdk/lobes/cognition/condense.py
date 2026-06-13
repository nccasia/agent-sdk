"""condense — B4 rewrite lobe: anaphoric follow-ups become standalone queries.

Behavior: `_should_condense`, decomposed — gated on the condense stage being
declared + session history present, then anaphora OR short query. Writes a
`retrieval_query` node (retrieval sees the rewrite; the answer prompt keeps
the original query). The `clarify` path biases it +0.25.

Signal weights: anaphora 0.6, short_query 0.6 (either alone clears the 0.5
threshold), has_history 0 (informational — a tuning lever).
Tuning keys: `prior_condense` (0), `min_condense` (0.5), `w_anaphora` (0.6),
`w_short_query` (0.6), `w_has_history` (0), `path_clarify__condense` (0.25).
Gates: degenerate-parity matrix (condense arm); attentionbench `tuning`.
"""

from __future__ import annotations

import logging

from agent_sdk.lobes.patterns import ANAPHORA_RE, _word_count
from agent_sdk.lobes.runtime import (
    BaseLobe,
    LlmCall,
    PromptContribution,
    TurnContext,
    extract_text,
)
from agent_sdk.network.activation import LAYER_COGNITION, LobeSpec

logger = logging.getLogger(__name__)

# ── Behavior contract ────────────────────────────────────────────────────────
# render_history(session_memory, last_k) -> the conversation block the rewrite
#   sees (summary + facts + last-k turn pairs, assistant turns clipped).
# run(llm, *, query, history, datetime_block) -> str | None — the standalone
#   rewrite, or None on empty/unchanged output or ANY failure (best-effort:
#   retrieval falls back to the raw query; a should-have-condensed None marks
#   the lobe `failed` in the flow). The datetime line rides along because the
#   rewrite resolves relative dates ("dời sang mai 9h") — the interpreter
#   renders it (benchmarks pin the clock by patching the interpreter's block).

SYSTEM_PROMPT = """You rewrite a user's latest chat message into a standalone search query.

The user is in an ongoing conversation. Their latest message may reference earlier turns with pronouns or demonstratives (e.g. "this course", "khóa này", "môn đó", "it"). Using the conversation provided, rewrite the latest message so it is fully self-contained: resolve every pronoun/reference to the explicit subject it refers to.

Rules:
- Keep the SAME language as the latest message.
- Preserve the user's intent exactly — do not answer the question, do not add new asks.
- If the message is already self-contained, return it unchanged.
- Output ONLY the rewritten query, nothing else."""

USER_TEMPLATE = "Conversation so far:\n{history}\n\nLatest message to rewrite:\n{query}"


def render_history(session_memory, last_k: int = 4) -> str:
    """The conversation block the rewrite reasons over — summary, known
    facts, then the last-k turn pairs (assistant openings carry the
    referent; clip at 600 chars). Whole block bounded to its 4000-char tail."""
    lines: list[str] = []
    if session_memory.summary:
        lines.append(f"(summary of earlier conversation) {session_memory.summary.strip()}")
    for k, v in (session_memory.facts or {}).items():
        lines.append(f"(known fact) {k}: {v}")
    for turn in session_memory.turns[-last_k:]:
        lines.append(f"User: {turn.user}")
        # Assistant turns can be long; the opening carries the referent.
        lines.append(f"Assistant: {turn.assistant[:600]}")
    return "\n".join(lines)[-4000:]


async def run(llm: LlmCall, *, query: str, history: str, datetime_block: str) -> str | None:
    """Rewrite an anaphoric follow-up into a standalone retrieval query.
    Legacy-exact: max_tokens=128, temperature=0, usage rolled up."""
    try:
        response = await llm(
            stage="condense",
            system=SYSTEM_PROMPT + "\n\n" + datetime_block,
            messages=[
                {"role": "user", "content": USER_TEMPLATE.format(history=history, query=query)}
            ],
            max_tokens=128,
            temperature=0,
        )
        condensed = extract_text(response).strip().strip('"')
        if not condensed or condensed == query:
            return None
        logger.info("condense: %r -> %r", query[:120], condensed[:120])
        return condensed
    except Exception:
        logger.exception("condense failed; retrieval falls back to raw query")
        return None


def signals(ctx: dict) -> dict[str, float]:
    """_should_condense, decomposed: gated on stage presence + history, then
    anaphora OR short query."""
    query = str(ctx.get("query") or "")
    stages = set(ctx.get("stages") or ())
    eligible = "condense" in stages and bool(ctx.get("has_history"))
    min_tokens = int(ctx.get("condense_min_tokens") or 6)
    anaphora = 1.0 if eligible and ANAPHORA_RE.search(query) else 0.0
    short = 1.0 if eligible and _word_count(query) < min_tokens else 0.0
    return {
        "anaphora": anaphora,
        "short_query": short,
        "has_history": 1.0 if ctx.get("has_history") else 0.0,
    }


SPEC = LobeSpec(
    id="condense",
    behavior="rewrite",
    layer=LAYER_COGNITION,
    order=0,
    prior=0.0,
    signals=signals,
    signal_weights={"anaphora": 0.6, "short_query": 0.6, "has_history": 0.0},
    writes=("retrieval_query",),
)


class CondenseLobe(BaseLobe):
    """Executable standalone-query rewrite lobe."""

    spec = SPEC
    SYSTEM_PROMPT = SYSTEM_PROMPT
    USER_TEMPLATE = USER_TEMPLATE

    def signals(self, ctx: dict) -> dict[str, float]:
        return signals(ctx)

    def prompt(self, _ctx: TurnContext) -> list[PromptContribution]:
        return [PromptContribution(SYSTEM_PROMPT, stability="volatile", source=self.id)]

    def render_history(
        self, session_memory, last_k: int = 4, *, _ctx: TurnContext | None = None
    ) -> str:
        return render_history(session_memory, last_k=last_k)

    async def run(
        self,
        llm: LlmCall,
        *,
        query: str,
        history: str,
        datetime_block: str,
        _ctx: TurnContext | None = None,
    ) -> str | None:
        return await run(llm, query=query, history=history, datetime_block=datetime_block)


LOBE = CondenseLobe()
