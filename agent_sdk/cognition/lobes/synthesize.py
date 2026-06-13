"""synthesize — B4 compose lobe: the answer composer.

Behavior: composes the grounded draft answer (`draft_answer` node). Pinned on
answer paths — B0 gates short-circuit BEFORE the network, so any turn that
reaches the network is an answer-producing turn and synthesize must run.

Note: pinned here means the runtime bypass in `propagate()`; the schema-level
`PINNED_LOBES` (validator-protected) is only {cite, filter} — a hostile
override against synthesize is accepted at save time yet harmless at runtime.

Tuning keys: `prior_synthesize` (1.0, informational — pinned bypasses it),
`path_qna__synthesize` (0.1), `budget_synthesize` (1600), layer segment
`budget_cognition` (600).
Gates: pinned-bypass hostile-weight test; degenerate-parity matrix.
"""

from __future__ import annotations

from agent_sdk.lobes.runtime import (
    BaseLobe,
    LlmCall,
    PromptContribution,
    TurnContext,
    extract_text,
)
from agent_sdk.network.activation import LAYER_COGNITION, LobeSpec

# ── Behavior contract ────────────────────────────────────────────────────────
# This lobe owns the answer-composition prompts for BOTH graphs:
#   SYSTEM_PROMPT / USER_TEMPLATE      — complex path (memo synthesis)
#   SIMPLE_SYSTEM_PROMPT / SIMPLE_USER_TEMPLATE — simple path (tool-loop
#       answer; the loop itself is orchestration: runtime.tool_loop)
# run(llm, *, query, memos, system, history, max_tokens) -> str — the memo
# synthesis call; ``system`` is the COMPOSED stage prompt (identity, skills,
# memory segments — built by the interpreter's prompt composer, which owns
# cross-lobe composition); ``history`` is the prior-turn message prefix.

SYSTEM_PROMPT = """You are a synthesis agent. Given several research memos, produce a coherent, well-structured answer.
Merge overlapping claims, note contradictions, and clearly distinguish verified from unverified information.
Output ONLY the synthesized answer in clean markdown. Do not reveal tool use or internal reasoning.

RULES:
- Only include claims that have supporting_chunk_ids — drop unsupported claims entirely.
- If the question contains a false premise, explicitly state it and provide the correct information from the evidence.
- If all memos have empty claims, refuse to answer rather than guessing.
- Prefer a concise, correct answer over a comprehensive but speculative one.
- Do NOT use markdown tables. Present comparisons and structured data as short bullet lists instead."""

USER_TEMPLATE = "Original query: {query}\n\nResearch memos:\n{memos}"

SIMPLE_SYSTEM_PROMPT = """You are a helpful enterprise assistant. Answer the user's question using ONLY information found through the retrieval tools.
Do not make up information. If the tools return no relevant results, explicitly say you cannot find the answer.
Always cite your sources using [chunk_id](source_ref) notation.

You have these retrieval tools:

DOCUMENT NAVIGATION:
- list_documents: See all documents in the knowledge base with titles and metadata
- browse_toc: View the Table of Contents of a document — see chapters, articles, sections
- search_toc: Search across all TOCs by heading text (e.g., "Điều 5", "tốt nghiệp")
- get_document_chunks: Read ALL chunks from a document (for summarization)
- read_page: Read all chunks from a specific page of a PDF

SEARCH & READ:
- semantic_search: Find chunks by meaning/concept similarity. Returns abbreviated snippets.
- keyword_search: Find chunks by exact keyword matching. Returns abbreviated snippets.
- read_chunk: Read the full text of specific chunks by ID. Use after search to get complete content.

STRATEGY — choose based on question type:
- Comparing specific articles (e.g. "Điều 1 vs Điều 5"): search_toc for EACH article → read_chunk on matched chunk_ids. Do NOT load the entire document.
- For specific articles/sections: search_toc with the article name → read_chunk on matched chunk IDs
- For structured documents (regulations, policies): browse_toc first → find relevant section → read_chunk
- For summarization of an entire document: list_documents → get_document_chunks
- For open-ended questions: semantic_search → keyword_search → read_chunk
- ALWAYS call read_chunk on relevant chunk IDs before answering (search tools return snippets only)
- EFFICIENCY: prefer search_toc + read_chunk (2 calls) over get_document_chunks (loads everything)
- NON-ENGLISH queries: lead with keyword_search and search_toc (exact lexical / heading match — robust across languages); semantic_search recall is weaker cross-lingual, so don't rely on it alone. If the first search misses, retry keyword_search on the key noun phrase (e.g. "bảo lưu", "tốt nghiệp") before giving up.

CRITICAL RULES:
- NEVER fabricate facts, numbers, dates, or names. Every claim must be supported by retrieved text.
- If the question contains a false premise (asks about something that doesn't exist or states something incorrect), say so and correct the premise using retrieved evidence.
- If multiple searches return no relevant chunks, refuse to answer rather than guess.
- Prefer precision over recall: a short correct answer beats a long speculative one.
- Do NOT use markdown tables. Present comparisons and structured data as short bullet lists instead."""

SIMPLE_USER_TEMPLATE = "Question: {query}"


def format_memos(memos: list) -> str:
    """The memo digest the synthesis call sees — aspect headers + claim
    bullets (unsupported claims are dropped later by the prompt's rules)."""
    return "\n".join(
        f"## {m.aspect_id}\n" + "\n".join(f"- {c.text}" for c in m.claims) for m in memos
    )


async def run(
    llm: LlmCall,
    *,
    query: str,
    memos: list,
    system: str | list,
    history: list[dict],
    max_tokens: int,
) -> str:
    """Compose the grounded answer from research memos (complex path).
    Legacy-exact: temperature=0.0, usage rolled up, history prepended."""
    msg = await llm(
        stage="synthesize",
        system=system,
        messages=[
            *history,
            {
                "role": "user",
                "content": USER_TEMPLATE.format(query=query, memos=format_memos(memos)),
            },
        ],
        max_tokens=max_tokens,
        temperature=0.0,
    )
    return extract_text(msg)


def signals(_ctx: dict) -> dict[str, float]:
    return {}  # pinned: activation is unconditional on answer paths


SPEC = LobeSpec(
    id="synthesize",
    behavior="compose",
    layer=LAYER_COGNITION,
    order=5,
    prior=1.0,
    pinned=True,  # pinned on answer paths (B0 gates short-circuit before the network)
    signals=signals,
    writes=("draft_answer",),
)


class SynthesizeLobe(BaseLobe):
    """Executable answer-composition lobe."""

    spec = SPEC
    SYSTEM_PROMPT = SYSTEM_PROMPT
    USER_TEMPLATE = USER_TEMPLATE
    SIMPLE_SYSTEM_PROMPT = SIMPLE_SYSTEM_PROMPT
    SIMPLE_USER_TEMPLATE = SIMPLE_USER_TEMPLATE

    def signals(self, ctx: dict) -> dict[str, float]:
        return signals(ctx)

    def prompt(self, ctx: TurnContext) -> list[PromptContribution]:
        stage = ctx.stage_id
        text = SIMPLE_SYSTEM_PROMPT if stage == "simple_answer" else SYSTEM_PROMPT
        return [PromptContribution(text, stability="stable", source=self.id)]

    def format_memos(self, memos: list) -> str:
        return format_memos(memos)

    async def run(
        self,
        llm: LlmCall,
        *,
        query: str,
        memos: list,
        system: str | list,
        history: list[dict],
        max_tokens: int,
        _ctx: TurnContext | None = None,
    ) -> str:
        return await run(
            llm,
            query=query,
            memos=memos,
            system=system,
            history=history,
            max_tokens=max_tokens,
        )


LOBE = SynthesizeLobe()
