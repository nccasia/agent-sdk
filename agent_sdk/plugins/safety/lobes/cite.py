"""cite — B5 ground lobe: citation extraction (grounding output-contract lobe).

Behavior: grounds the answer in retrieved sources (`citation` nodes). One of the
`OUTPUT_CONTRACT_LOBES` (agent_sdk.network.activation): activation is driven by the
resolved path's `grounds` flag — live on grounding paths (qna/research), dark on
non-grounding ones (onboarding/relational/manage/…). The gate in `propagate()`
makes this weight-immune, so no `flow_lobe_weight` can flip it on a grounding
path. The actual ground-or-refuse SAFETY contract lives in the interpreter
(`enforce_citations`), independent of this lobe's activation.

`tools_used` is emitted but weighted 0 — purely informational in the trace (it
is not known at dispatch time, before retrieval runs).

Tuning keys: none that change activation (path-gated). `w_tools_used` (0),
`budget_cite` (1600).
Gates: grounding-path activation test; degenerate-parity matrix.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import numpy as np

from agent_sdk.contracts.memo import Citation
from agent_sdk.lobes.runtime import (
    BaseLobe,
    LlmCall,
    PromptContribution,
    TurnContext,
    extract_text,
)
from agent_sdk.network.activation import LAYER_EXPRESSION, LobeSpec

logger = logging.getLogger(__name__)

# ── Behavior contract ────────────────────────────────────────────────────────
# verify_claims(llm, *, memos)      — complex path: LLM-verify the flattened
#   memo claims; unparseable output keeps the input claims (fail-open).
# relevant_citations(llm, *, ...)   — simple path post-filter, two layers:
#   1) embedding pre-cut against the answer (threshold, fail-open),
#   2) ONE binary YES/NO verdict per surviving source (source_relevant) —
#      affirmative keep; a raised verdict KEEPS the candidate (can't-judge is
#      not judged-irrelevant — never hide real grounding on a provider error).
# Display honesty only: the ground-or-refuse decision stays on the RAW
# citation set in the orchestration.

SYSTEM_PROMPT = """You are a citation verification agent. Review each claim and its supporting spans.
Drop any claim that lacks supporting evidence. Keep only verified claims with valid citations.
Respond with a JSON object with a "verified_claims" list. Each claim must have text, supporting_chunk_ids, and confidence."""

USER_TEMPLATE = "Claims to verify:\n{claims}"

# Flow-axis grounding pass (the "cite" FlowStep, _run_pipeline). The step's
# system prompt carries the prior steps' outputs ("## Step output — …") and
# the evidence index ("## Evidence index …"); its text IS the next pipeline
# state, so it must emit the user-facing answer — never a JSON verdict.
FLOW_GROUND_PROMPT = """You are the citation grounding pass of a research pipeline.
The system context carries the synthesized answer (under "## Step output — synthesize")
and the evidence index of chunks actually read (under "## Evidence index").

Rewrite the synthesized answer as the FINAL user-facing answer, in the user's language:
- Keep every claim a chunk in the evidence index supports, citing it inline as [chunk_id].
- Drop claims no chunk supports. Never invent chunk ids.
- Preserve the answer's content and tone otherwise. Output ONLY the final answer —
  no preamble, no verdict, no JSON."""

SOURCE_RELEVANCE_PROMPT = """You verify whether an assistant's reply actually uses a source document.

You get the reply and ONE source excerpt. Answer YES only if the reply conveys specific information found in this excerpt — a rule, a number, a date, a procedure, a definition. Merely mentioning a topic by name, greeting the user, or listing capabilities is NOT using the source.

Answer with exactly one word: YES or NO."""

SOURCE_RELEVANCE_USER_TEMPLATE = "Reply:\n{answer}\n\nSource excerpt (doc: {doc}):\n{excerpt}"


async def verify_claims(llm: LlmCall, *, memos: list) -> list[dict]:
    """Complex-path claim verification. Legacy-exact: max_tokens=1024,
    temperature=0, no usage roll-up, unparseable → input claims unchanged."""
    claims = [
        {"text": c.text, "supporting_chunk_ids": c.supporting_chunk_ids}
        for m in memos
        for c in m.claims
    ]
    if not claims:
        return []
    msg = await llm(
        stage="cite",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": USER_TEMPLATE.format(claims=str(claims))}],
        max_tokens=1024,
        temperature=0.0,
        count_usage=False,
    )
    try:
        return json.loads(extract_text(msg)).get("verified_claims", [])
    except Exception:
        return claims


async def relevant_citations(
    llm: LlmCall,
    *,
    answer_text: str,
    retrieved_chunks: list[dict],
    all_cites: list[Citation],
    threshold: float,
    semaphore: asyncio.Semaphore,
    encode: Any | None = None,
) -> list[Citation]:
    """Two-layer relevance post-filter for answers with no inline markers.
    ``encode(texts, normalize_embeddings=True)`` defaults to the local
    embedding model; inject a fake in tests."""
    texts = {c["chunk_id"]: c.get("text") or "" for c in retrieved_chunks}
    kept = all_cites
    try:
        if encode is None:
            # Leaf SDK: no bundled embedding model — keep all references unless
            # the host injects ``encode`` (e.g. via services.embed) to enable the
            # embedding-based citation post-filter.
            return kept
        payload = [answer_text] + [texts.get(c.chunk_id, "") for c in all_cites]
        embs = np.asarray(
            await asyncio.to_thread(encode, payload, normalize_embeddings=True),
            dtype="float32",
        )
        a_vec, chunk_vecs = embs[0], embs[1:]
        scores = chunk_vecs @ a_vec
        kept = [c for c, s in zip(all_cites, scores, strict=True) if float(s) >= threshold]
        if len(kept) < len(all_cites):
            logger.info(
                "citation post-filter: kept %d/%d references (threshold=%.2f)",
                len(kept),
                len(all_cites),
                threshold,
            )
    except Exception:
        logger.exception("citation relevance scoring failed — keeping all references")
    if not kept:
        return []
    return await _llm_filter(
        llm, answer_text=answer_text, chunk_texts=texts, candidates=kept, semaphore=semaphore
    )


async def _llm_filter(
    llm: LlmCall,
    *,
    answer_text: str,
    chunk_texts: dict[str, str],
    candidates: list[Citation],
    semaphore: asyncio.Semaphore,
) -> list[Citation]:
    """One BINARY relevance verdict per candidate, in parallel — kept only on
    a parseable YES; a raised call keeps the candidate."""
    # The reply is the same in every verdict; excerpts are per-candidate.
    answer = answer_text[:1500]

    async def _verify(c: Citation) -> bool:
        async with semaphore:
            resp = await llm(
                stage="filter",
                system=SOURCE_RELEVANCE_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": SOURCE_RELEVANCE_USER_TEMPLATE.format(
                            answer=answer,
                            doc=c.source_ref,
                            excerpt=(chunk_texts.get(c.chunk_id) or "")[:500],
                        ),
                    }
                ],
                max_tokens=3,
            )
        verdict = extract_text(resp).strip().upper()
        keep = verdict.startswith("YES")
        logger.info("source relevance: doc=%s verdict=%r kept=%s", c.source_ref, verdict[:20], keep)
        return keep

    results = await asyncio.gather(*(_verify(c) for c in candidates), return_exceptions=True)
    kept: list[Citation] = []
    for c, res in zip(candidates, results, strict=True):
        if isinstance(res, BaseException):
            logger.warning(
                "source relevance check failed for doc=%s — keeping it",
                c.source_ref,
                exc_info=res,
            )
            kept.append(c)
        elif res:
            kept.append(c)
    logger.info("citation LLM filter: kept %d/%d references", len(kept), len(candidates))
    return kept


def signals(ctx: dict) -> dict[str, float]:
    # Informational only — cite's activation is driven by the resolved path's
    # grounding flag (OUTPUT_CONTRACT_LOBES gate in propagate()), not by signals.
    return {"tools_used": 1.0 if ctx.get("tools_used") else 0.0}


SPEC = LobeSpec(
    id="cite",
    behavior="ground",
    layer=LAYER_EXPRESSION,
    order=0,
    prior=0.0,  # activation is path-grounds-gated, not prior-driven
    pinned=False,  # grounding output-contract lobe — see OUTPUT_CONTRACT_LOBES
    signals=signals,
    writes=("citation",),
)


class CiteLobe(BaseLobe):
    """Executable citation grounding lobe."""

    spec = SPEC
    SYSTEM_PROMPT = SYSTEM_PROMPT
    USER_TEMPLATE = USER_TEMPLATE
    SOURCE_RELEVANCE_PROMPT = SOURCE_RELEVANCE_PROMPT
    SOURCE_RELEVANCE_USER_TEMPLATE = SOURCE_RELEVANCE_USER_TEMPLATE

    def signals(self, ctx: dict) -> dict[str, float]:
        return signals(ctx)

    def prompt(self, _ctx: TurnContext) -> list[PromptContribution]:
        return [PromptContribution(SYSTEM_PROMPT, stability="stable", source=self.id)]

    async def verify_claims(
        self, llm: LlmCall, *, memos: list, _ctx: TurnContext | None = None
    ) -> list[dict]:
        return await verify_claims(llm, memos=memos)

    async def relevant_citations(
        self,
        llm: LlmCall,
        *,
        answer_text: str,
        retrieved_chunks: list[dict],
        all_cites: list[Citation],
        threshold: float,
        semaphore: asyncio.Semaphore,
        encode: Any | None = None,
        _ctx: TurnContext | None = None,
    ) -> list[Citation]:
        return await relevant_citations(
            llm,
            answer_text=answer_text,
            retrieved_chunks=retrieved_chunks,
            all_cites=all_cites,
            threshold=threshold,
            semaphore=semaphore,
            encode=encode,
        )


LOBE = CiteLobe()
