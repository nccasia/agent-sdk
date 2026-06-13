"""research — B4 fanout lobe: per-aspect retrieval sub-agents.

Behavior: signal-less, purely edge-driven — runs iff `plan` ran (the
degenerate chain). Raw KB chunks enter ONLY this lobe's receptive field; only
memo-shaped nodes are written back (`Blackboard.write_back` rejects
RAW_CHUNK_KINDS outright) — the prd.md §10 compression invariant enforced by
the data flow instead of by review.

Tuning keys: `prior_research` (0), `min_research` (0.5),
`edge_plan__research` (1.0), `edge_research__synthesize` (1.0),
`path_research__research` (0.2), `budget_research` (1600).
Gates: degenerate-parity matrix; attentionbench `bounded` (raw-chunk
confinement).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from agent_sdk.contracts.memo import Claim, Memo
from agent_sdk.lobes.runtime import (
    BaseLobe,
    LlmCall,
    PromptContribution,
    TurnContext,
    extract_text,
    tool_loop,
)
from agent_sdk.network.activation import LAYER_COGNITION, LobeSpec
from agent_sdk.network.context_builder import ContextNode

# ── Behavior contract ────────────────────────────────────────────────────────
# run_aspect(llm, *, aspect, tools, execute_tools, assistant_content, ...)
#   drives one retrieval sub-agent and returns exactly one Memo. ``execute_tools``
#   is injected by the interpreter because only orchestration owns tool runtime
#   state and retrieved/read tracking.
# run(...) bounds fanout and gathers aspects concurrently through an injected
#   semaphore. Exceptions from one aspect are dropped (legacy gather behavior).
# nodes(memos) writes memo-shaped context only — raw chunks never leave this
#   lobe's receptive field.

SYSTEM_PROMPT = """You are a research sub-agent. Your job is to gather factual information about a specific aspect of a question.

You have three retrieval tools:
- semantic_search: Find chunks by meaning similarity. Returns abbreviated snippets.
- keyword_search: Find chunks by keyword matching. Takes a list of short keywords. Returns abbreviated snippets.
- read_chunk: Read full text of chunks by ID. MUST use after search to get complete content.

IMPORTANT: Search tools return snippets only. Always call read_chunk before forming claims.

Strategy:
1. Search broadly first (semantic_search), then narrow with keyword_search for specific terms
2. Read the top 2-3 most relevant chunks in full before forming any claims
3. If initial search yields poor results, try alternative phrasings or related terms
4. Only make claims directly supported by read_chunk content — never extrapolate

When done, return a JSON memo:
{{"aspect_id": "...", "claims": [{{"text": "...", "supporting_chunk_ids": ["..."]}}], "unresolved": []}}

Mark a claim as unresolved if evidence is ambiguous or contradictory. An empty claims list with clear unresolved items is better than hallucinated claims."""

USER_TEMPLATE = "Aspect: {aspect_id}\nQuestion: {question}"


async def run_aspect(
    llm: LlmCall,
    *,
    aspect: dict,
    tools: list[dict],
    execute_tools: Callable[[Any, list[dict], set[str]], Awaitable[list[dict]]],
    assistant_content: Callable[[Any], list[dict]],
    max_loops: int,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> Memo:
    """Run one per-aspect retrieval sub-agent and parse its JSON memo."""
    messages: list[dict] = [
        {
            "role": "user",
            "content": USER_TEMPLATE.format(aspect_id=aspect["id"], question=aspect["question"]),
        },
    ]
    retrieved: list[dict] = []
    already_read: set[str] = set()

    async def call(loop_messages: list[dict], loop_tools: list[dict]):
        msg = await llm(
            stage="research",
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=loop_messages,
            tools=loop_tools,
            temperature=temperature,
            count_usage=False,
        )
        return msg, ""

    async def execute(msg: Any) -> list[dict]:
        return await execute_tools(msg, retrieved, already_read)

    msg, _ = await tool_loop(
        call,
        messages=messages,
        tools=tools,
        execute_tools=execute,
        assistant_content=assistant_content,
        max_loops=max_loops,
        strict_end_turn=False,
    )

    text = extract_text(msg)
    try:
        data = json.loads(text)
        return Memo(
            aspect_id=aspect["id"],
            claims=[
                Claim(
                    text=c["text"],
                    supporting_chunk_ids=c.get("supporting_chunk_ids", []),
                    confidence=c.get("confidence", 0.9),
                )
                for c in data.get("claims", [])
            ],
            unresolved=data.get("unresolved", []),
            tokens_used=msg.usage.input_tokens + msg.usage.output_tokens,
        )
    except Exception:
        return Memo(
            aspect_id=aspect["id"],
            claims=[],
            unresolved=[str(text)[:200]],
            tokens_used=0,
        )


async def run(
    llm: LlmCall,
    *,
    aspects: list[dict],
    tools: list[dict],
    execute_tools: Callable[[Any, list[dict], set[str]], Awaitable[list[dict]]],
    assistant_content: Callable[[Any], list[dict]],
    semaphore: asyncio.Semaphore,
    fanout_max: int,
    max_loops: int,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> list[Memo]:
    """Run research fanout with legacy gather(return_exceptions=True) behavior."""

    async def guarded(aspect: dict) -> Memo:
        async with semaphore:
            return await run_aspect(
                llm,
                aspect=aspect,
                tools=tools,
                execute_tools=execute_tools,
                assistant_content=assistant_content,
                max_loops=max_loops,
                max_tokens=max_tokens,
                temperature=temperature,
            )

    results = await asyncio.gather(
        *(guarded(a) for a in aspects[:fanout_max]), return_exceptions=True
    )
    return [r for r in results if isinstance(r, Memo)]


def nodes(memos: list[Memo]) -> list[ContextNode]:
    """Memo write-back nodes; raw chunks stay confined to research."""
    return [
        ContextNode(
            id=f"memo:{m.aspect_id}",
            kind="memo",
            text="\n".join(c.text for c in m.claims)[:1000],
        )
        for m in memos
    ]


def signals(_ctx: dict) -> dict[str, float]:
    return {}  # edge-driven: runs iff plan ran (degenerate chain)


SPEC = LobeSpec(
    id="research",
    behavior="fanout",
    layer=LAYER_COGNITION,
    order=4,
    prior=0.0,
    signals=signals,
    # Raw chunks enter ONLY this receptive field; only memo-shaped
    # nodes are written back (compression invariant, structural).
    edges={"synthesize": 1.0},
    writes=("memo",),
)


class ResearchLobe(BaseLobe):
    """Executable per-aspect retrieval fanout lobe."""

    spec = SPEC
    SYSTEM_PROMPT = SYSTEM_PROMPT
    USER_TEMPLATE = USER_TEMPLATE

    def signals(self, ctx: dict) -> dict[str, float]:
        return signals(ctx)

    def prompt(self, _ctx: TurnContext) -> list[PromptContribution]:
        return [PromptContribution(SYSTEM_PROMPT, stability="stable", source=self.id)]

    async def run_aspect(
        self,
        llm: LlmCall,
        *,
        aspect: dict,
        tools: list[dict],
        execute_tools: Callable[[Any, list[dict], set[str]], Awaitable[list[dict]]],
        assistant_content: Callable[[Any], list[dict]],
        max_loops: int,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        _ctx: TurnContext | None = None,
    ) -> Memo:
        return await run_aspect(
            llm,
            aspect=aspect,
            tools=tools,
            execute_tools=execute_tools,
            assistant_content=assistant_content,
            max_loops=max_loops,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def run(
        self,
        llm: LlmCall,
        *,
        aspects: list[dict],
        tools: list[dict],
        execute_tools: Callable[[Any, list[dict], set[str]], Awaitable[list[dict]]],
        assistant_content: Callable[[Any], list[dict]],
        semaphore: asyncio.Semaphore,
        fanout_max: int,
        max_loops: int,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        _ctx: TurnContext | None = None,
    ) -> list[Memo]:
        return await run(
            llm,
            aspects=aspects,
            tools=tools,
            execute_tools=execute_tools,
            assistant_content=assistant_content,
            semaphore=semaphore,
            fanout_max=fanout_max,
            max_loops=max_loops,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def nodes(self, memos: list[Memo], *, _ctx: TurnContext | None = None) -> list[ContextNode]:
        return nodes(memos)


LOBE = ResearchLobe()
