"""memory_recall — B2 recall lobe for scoped context entries (the `## Memory` block).

Behavior: loads the unified memory index (`_load_context_index`) and writes
`memory` nodes back to the blackboard. Gated by the policy's memory master
switch at parity; a quiet lobe skips the backend call AND its prompt segment.

Tuning keys: `prior_memory_recall` (0), `min_memory_recall` (0.5),
`w_memory_enabled` (1.0), `w_mem_conversation/channel/user/bot` (0 at parity —
the scoped-context levers), `budget_memory_recall` (1600).
Gates: degenerate-parity matrix; attentionbench `bounded` / `flood`.

Phase 4+ — the lobe is a state machine of two opt-in nodes:

- ``memory:scoped`` — the structured per-scope memory entries (1
  ContextNode per entry). Fires when ``memory_enabled`` is true and the
  conversation has scoped memory items. Per-bot disable:
  ``disable_memory_recall_memory:scoped``.
- ``memory:index`` — the rendered memory index block (the legacy
  `## Memory` header + text). Fires when the index block is non-empty.
  Per-bot disable: ``disable_memory_recall_memory:index`` (when a bot
  wants only the structured entries, not the rendered text).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from agent_sdk.lobes.runtime import BaseLobe, PromptContribution, TurnContext
from agent_sdk.network.activation import LAYER_MEMORY, ContextBound, LobeNode, LobeSpec
from agent_sdk.network.context_builder import ContextNode

MEMORY_CUE_EMPTY = (
    "## Memory\n"
    "No remembered facts yet. When the user asks you to "
    "remember something, or the conversation produces a "
    "durable fact (meeting summary, decision, preference, "
    "team convention, deploy schedule), store it NOW with "
    "memory {action:'save', scope, key, value, description}."
)

MEMORY_HEADER_READONLY = (
    "## Memory\n"
    "Facts pinned by this bot's admin — treat them as "
    "authoritative, current facts when answering. "
    "(Read-only this turn.)\n"
)

MEMORY_HEADER_ACTIVE = (
    "## Memory\n"
    "Chat history is NOT memory — when the user reports ANY "
    "change or new detail for a tracked fact (an assignment, a "
    "status change, a block, a risk, a member change), you MUST "
    "memory {action:'save'} it in THIS turn even though the "
    "conversation already mentions it. Saving the SAME scope+key "
    "replaces the fact completely — write the new current value, "
    "never a change narrative; the outdated value must not "
    "remain. Values below are shown inline — answer from them "
    "directly; memory {action:'recall'} is ONLY for entries "
    "marked truncated:\n"
)


def chrome(skill_active: bool, *, has_entries: bool) -> str:
    """The functional `## Memory` header/cue, independent of selected nodes."""
    if not has_entries:
        return MEMORY_CUE_EMPTY if skill_active else ""
    return MEMORY_HEADER_ACTIVE if skill_active else MEMORY_HEADER_READONLY


def nodes(items: list[dict], index_block: str = "") -> list[ContextNode]:
    out = [
        ContextNode(
            id=f"memory:{item.get('scope') or 'bot'}/{item.get('key') or i}",
            kind="memory",
            text=str(item.get("value") or item.get("description") or "")[:200],
            scope=str(item.get("scope") or "bot"),
        )
        for i, item in enumerate(items)
    ]
    if not out and index_block:
        out = [ContextNode(id="memory:index", kind="memory", text=index_block[:400])]
    return out


async def load(
    post_fn: Callable[[str, dict], Awaitable[dict[str, Any]]],
    *,
    tenant_id: str,
    bot_id: str,
    scopes: list[dict],
    structured: bool,
    skill_active: bool,
) -> tuple[str, list[dict]]:
    """Load and render the scoped memory index through an injected backend client."""
    data = await post_fn(
        "/v1/internal/context/index",
        {
            "tenant_id": tenant_id,
            "bot_id": bot_id,
            "scopes": scopes,
            "structured": structured,
        },
    )
    items = list(data.get("items") or []) if structured else []
    index = (data.get("index") or "").strip()
    header = chrome(skill_active, has_entries=bool(index))
    if not header:
        return "", items
    return (header + index if index else header), items


def signals(ctx: dict) -> dict[str, float]:
    """memory_enabled gates (parity); the per-scope presence signals expose
    the scoped context hierarchy (conversation > channel > user > bot) as
    tuning levers — weight 0 at parity defaults, so a bot can e.g. boost
    recall when the CURRENT CONVERSATION holds scoped memories."""
    scopes = ctx.get("memory_scopes") or {}
    out = {"memory_enabled": 1.0 if ctx.get("memory_enabled", True) else 0.0}
    for scope in ("conversation", "channel", "user", "bot"):
        out[f"mem_{scope}"] = 1.0 if scopes.get(scope) else 0.0
    return out


# Phase 4+ — per-lobe signal vocabulary for the memory state machine.
def _memory_signal_ctx(ctx: TurnContext) -> dict:
    """Build the memory-signal dict from the TurnContext.

    Two flags the memory lobe reads:

    - memory_enabled: the policy's memory master switch is on
    - has_scoped_entries: at least one scoped memory item exists
    - has_index_block: the rendered ## Memory block is non-empty
    """
    items = list(ctx.memory_items or ())
    index_block = str(ctx.lobe_outputs.get("memory_recall") or "")
    policy = ctx.policy if isinstance(ctx.policy, dict) else {}
    memory_on = bool(policy.get("memory_enabled", True))
    return {
        "memory_enabled": 1.0 if memory_on else 0.0,
        "has_scoped_entries": 1.0 if items else 0.0,
        "has_index_block": 1.0 if index_block else 0.0,
    }


def _signals_for(ctx_dict: dict, node_id: str) -> dict[str, float]:
    if node_id == "memory:scoped":
        return {"has_scoped_entries": 1.0 if ctx_dict.get("has_scoped_entries") else 0.0}
    if node_id == "memory:index":
        return {"has_index_block": 1.0 if ctx_dict.get("has_index_block") else 0.0}
    return {}


def _node_memory_scoped(lobe_id: str) -> LobeNode:
    """The structured per-scope memory entries — 1 ContextNode per item."""

    def _produce(ctx: TurnContext) -> list[ContextNode]:
        items = list(ctx.memory_items or ())
        if not items:
            return []
        return nodes(items)

    def _signals(_ctx: dict) -> dict[str, float]:
        return _signals_for(_ctx, "memory:scoped")

    return LobeNode(
        id="memory:scoped",
        lobe_id=lobe_id,
        layer=LAYER_MEMORY,
        stability="slow",
        prior=0.0,
        signals=_signals,
        signal_weights={"has_scoped_entries": 1.0},
        min_activation=0.5,
        order=0,
        description="structured per-scope memory entries (1 ContextNode per item)",
        produce=_produce,
        prompt=lambda _ctx: [],
    )


def _node_memory_index(lobe_id: str) -> LobeNode:
    """The rendered ## Memory index block (the legacy prompt-text path)."""

    def _produce(ctx: TurnContext) -> list[ContextNode]:
        block = str(ctx.lobe_outputs.get("memory_recall") or "")
        if not block:
            return []
        return [ContextNode(id="memory:index", kind="memory", text=block[:400])]

    def _signals(_ctx: dict) -> dict[str, float]:
        return _signals_for(_ctx, "memory:index")

    return LobeNode(
        id="memory:index",
        lobe_id=lobe_id,
        layer=LAYER_MEMORY,
        stability="slow",
        prior=0.0,
        signals=_signals,
        signal_weights={"has_index_block": 1.0},
        min_activation=0.5,
        order=1,
        description="the rendered ## Memory index block (legacy prompt-text path)",
        produce=_produce,
        prompt=lambda _ctx: [],
    )


SPEC = LobeSpec(
    id="memory_recall",
    behavior="recall",
    layer=LAYER_MEMORY,
    order=0,
    prior=0.0,
    signals=signals,
    attends=ContextBound(kinds=("memory",)),
    writes=("memory",),
)


class MemoryRecallLobe(BaseLobe):
    """Executable scoped-memory recall lobe (Phase 4+ state machine)."""

    spec = SPEC
    MEMORY_CUE_EMPTY = MEMORY_CUE_EMPTY
    MEMORY_HEADER_READONLY = MEMORY_HEADER_READONLY
    MEMORY_HEADER_ACTIVE = MEMORY_HEADER_ACTIVE

    def chrome(self, skill_active: bool, *, has_entries: bool) -> str:
        return chrome(skill_active, has_entries=has_entries)

    def signals(self, ctx: dict) -> dict[str, float]:
        return signals(ctx)

    def prompt(self, ctx: TurnContext) -> list[PromptContribution]:
        index_block = str(ctx.lobe_outputs.get(self.id) or "")
        if not index_block:
            return []
        return [PromptContribution(index_block, stability="slow", source=self.id)]

    async def load(
        self,
        post_fn: Callable[[str, dict], Awaitable[dict[str, Any]]],
        *,
        tenant_id: str,
        bot_id: str,
        scopes: list[dict],
        structured: bool,
        skill_active: bool,
        _ctx: TurnContext | None = None,
    ) -> tuple[str, list[dict]]:
        return await load(
            post_fn,
            tenant_id=tenant_id,
            bot_id=bot_id,
            scopes=scopes,
            structured=structured,
            skill_active=skill_active,
        )

    def nodes(
        self, items: list[dict], index_block: str = "", *, _ctx: TurnContext | None = None
    ) -> list[ContextNode]:
        return nodes(items, index_block)

    def _signal_ctx_for(self, ctx: TurnContext) -> dict:
        """Override the default — the memory lobe injects memory_enabled /
        has_scoped_entries / has_index_block signal vocabulary on top of
        the cross-section defaults."""
        base = super()._signal_ctx_for(ctx)
        base.update(_memory_signal_ctx(ctx))
        return base

    def state_machine(self) -> list[LobeNode]:
        """Two opt-in nodes — the memory lobe's state machine."""
        return [
            _node_memory_scoped(self.id),
            _node_memory_index(self.id),
        ]


LOBE = MemoryRecallLobe()
