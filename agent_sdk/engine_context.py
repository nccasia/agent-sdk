"""The dynamic context pipeline — lobe → ContextNode → attention → tiered render.

Phase-0 enabler: a stage's lobes can emit ``ContextNode``s (via
``Lobe.build_context``); the engine pools them, scores + selects them under a
token budget (``build_attention``), routes the survivors to exposure tiers
(``route_tiers``: inject / hint / offload), and renders them into the system
prompt grouped by the producing lobe (so the Prompt-panel provenance overlay
colours by lobe). Pure + deterministic; ``q_vec=None`` ⇒ L1-only.

This is a no-op for lobes that don't override ``build_context`` (the default
7-lobe network + the coding-agent lobes return ``[]``), so it is fully
backward-compatible — it only contributes when a node-emitting lobe (skills,
memory, task) is present.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_sdk.contracts.turn import TurnContext
from agent_sdk.network.context_builder import (
    TIER_HINT,
    TIER_INJECT,
    ContextNode,
    _node_menu_hint,
    build_attention,
    merge_weights,
    route_tiers,
)

__all__ = ["build_turn_context", "collect_nodes", "select_and_render"]


def build_turn_context(
    query: str,
    state: Any,
    *,
    scratchpad: Any = None,
    snapshot: dict | None = None,
    stage_id: str | None = None,
    path: str | None = None,
    active_lobes: frozenset[str] | tuple[str, ...] = (),
    lobe_outputs: dict | None = None,
    policy: dict | None = None,
) -> TurnContext:
    """Assemble the per-turn ``TurnContext`` the node-emitting lobes read.

    ``snapshot`` carries async-prefetched data (memory_items, task_items, …) so
    ``build_context`` stays a pure, sync function of the context.
    """
    snap = snapshot or {}
    return TurnContext(
        query=query,
        policy=policy or {},
        stage_id=stage_id,
        active_path=path,
        active_lobes=frozenset(active_lobes),
        scratchpad=scratchpad,
        session_memory=state,
        memory_items=snap.get("memory_items", ()),
        task_items=snap.get("task_items", ()),
        catalog_items=snap.get("catalog_items", ()),
        identity=snap.get("identity", {}),
        channel=snap.get("channel", {}),
        lobe_outputs=dict(lobe_outputs or {}),
    )


def collect_nodes(
    stage_lobe_ids: tuple[str, ...],
    lobe_by_id: dict[str, Any],
    turn_ctx: TurnContext,
) -> list[tuple[str, ContextNode]]:
    """Run each stage lobe's ``build_context`` and tag nodes with their producer."""
    out: list[tuple[str, ContextNode]] = []
    for lobe_id in stage_lobe_ids:
        lobe = lobe_by_id.get(lobe_id)
        if lobe is None:
            continue
        try:
            nodes = list(lobe.build_context(turn_ctx) or [])
        except Exception:
            nodes = []
        for node in nodes:
            if isinstance(node, ContextNode):
                out.append((lobe_id, node))
    return out


def select_and_render(
    producer_nodes: list[tuple[str, ContextNode]],
    q_text: str,
    q_vec: Any | None = None,
    *,
    weights: dict[str, float] | None = None,
    budget_tokens: int = 8000,
    min_activation: float = 0.0,
    embed_batch: Callable | None = None,
    trace_out: dict | None = None,
) -> list[tuple[str, str, str]]:
    """Select + tier the pooled nodes; return ``[(source, text, stability)]`` parts.

    The engine appends these parts to its system-prompt accumulator, so offsets
    + provenance segments stay global. ``source`` is the producing lobe id (so
    the Prompt panel colours by lobe); hint-tier nodes collapse into one
    ``hints`` references block.

    ``trace_out``, when provided, is populated in place with the attention/tier
    telemetry (per-node ``{id,kind,activation,utility,cds,tokens,tier}`` +
    ``tier_counts``) so the engine can record it on the stage trace. Read-only
    side effect — does not change the return value, so every existing caller is
    byte-identical.
    """
    if not producer_nodes:
        return []
    w = merge_weights(weights)
    nodes = [n for _, n in producer_nodes]
    producer = {id(n): lobe for lobe, n in producer_nodes}

    selected, attn_trace = build_attention(
        nodes, q_text, q_vec, weights=w, budget_tokens=budget_tokens,
        min_activation=min_activation, embed_batch=embed_batch,
    )
    _, tier_trace = route_tiers(selected, weights=w, budget_tokens=budget_tokens)
    if trace_out is not None:
        trace_out["nodes"] = tier_trace["nodes"]
        trace_out["tiers"] = tier_trace["nodes"]  # alias for the viewer's tier panel
        trace_out["tier_counts"] = tier_trace["tier_counts"]
        trace_out["total_tokens"] = attn_trace.get("total_tokens", 0)
        trace_out["tier1_tokens"] = tier_trace.get("tier1_tokens", 0)
        trace_out["budget_tokens"] = budget_tokens

    parts: list[tuple[str, str, str]] = []
    ordered = sorted(selected, key=lambda n: n._order)
    for node in ordered:
        if node.tier in (0, TIER_INJECT):
            text = (node.text or "").strip()
            if text:
                parts.append((producer.get(id(node), "context"), text, node.stability or "slow"))
    hints = [_node_menu_hint(n) for n in ordered if n.tier == TIER_HINT]
    hints = [h for h in hints if h]
    if hints:
        parts.append((
            "hints",
            "References (recall to read with the memory/read tools):\n" + "\n".join(hints),
            "turn",
        ))
    return parts
