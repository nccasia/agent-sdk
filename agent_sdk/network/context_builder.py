"""Adaptive context builder — the attention layer over informational context.

Context engineering as a layered node graph instead of static chains: every
informational prompt fragment (a memory entry, a resolved variable, a session
fact, the identity block…) is a :class:`ContextNode` with an ACTIVATION
computed in three layers:

  L1  lexical/structural — NFC token overlap with the turn query, plus
      admin/scope/recency boosts. Free and deterministic.
  L2  semantic — cosine similarity between the node's embedding and the turn
      query vector (the same bge-small embedding the engine already computes
      once per turn). Catches paraphrase/vi↔en relevance L1 misses.
  L3  budgeted selection — pinned nodes always; below-threshold nodes dropped;
      greedy skip-not-stop fill under ``context_budget_tokens``.

The prompt keeps the ORIGINAL composition order (selection decides inclusion,
not ordering) and nodes carry a ``stability`` class (static|slow|turn) so the
composer can order static→dynamic for provider prompt-cache friendliness.

Weights are the node-by-node optimization surface: ``DEFAULT_NODE_WEIGHTS``
committed here, sparsely overridable per bot via ``policy.context_weights``.
Pure functions — deterministic given (nodes, q_vec, weights, budget), no
clock, no I/O (embeddings are injected).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_sdk.skills import est_tokens

# Stability classes for cache-aware composition (static prefix first).
STABILITY_ORDER = {"static": 0, "slow": 1, "turn": 2}

# Hard cap on nodes assembled per turn (embedding cost bound) — overflow is
# dropped lowest-kind-prior first before scoring.
MAX_NODES = 64

DEFAULT_NODE_WEIGHTS: dict[str, float] = {
    # layer weights
    "w_l1": 1.0,
    "w_l2": 1.2,  # semantic slightly favored — catches "who is here" → channel_members
    # Cosine calibration: bge-small scores unrelated texts ~0.4-0.6; raw cosine
    # rewards every node. l2 = max(0, (cos - floor) / (1 - floor)).
    "l2_floor": 0.55,
    # L1 sub-boosts
    "admin_boost": 0.40,
    "scope_conversation": 0.15,
    "scope_channel": 0.10,
    "scope_user": 0.05,
    "scope_bot": 0.0,
    "recency_boost": 0.10,  # × recency_rank (0..1), memory nodes only
    # Selection threshold — sits ABOVE the bare memory/fact priors so a
    # zero-signal flooder never rides its prior into the prompt (the
    # attentionbench efficiency finding: greedy fill packed weak nodes).
    "min_activation": 0.22,
    # Per-kind sparsification (attention top-k): flood-prone kinds keep only
    # their k highest-activation nodes — relative competition, not absolute
    # thresholds (the vi-vi cosine baseline defeats any single floor; the
    # relevant node always wins its kind by a wide margin).
    "topk_memory": 7,
    "topk_session_fact": 4,
    "topk_task_state": 4,
    # Spreading activation (the propagation layer): the top-scored nodes
    # spread activation to nodes sharing rare tokens with them — captures
    # 2-hop relevance the query alone can't see (agenda mentions "OAuth" →
    # the oauth_status node lights up; the incident runbook says "rollback
    # deploy" → the last_deploy fact lights up).
    "w_spread": 0.85,
    "spread_top_n": 3,
    # A node spreads only when the QUERY actually lit it up (lexical overlap
    # + semantic), not when it merely rides structural boosts.
    "spread_source_min": 0.1,
    # Self-referential queries ("mình", "tôi", "I", "my", …) boost user-scope
    # nodes — "what should I hand over?" is ABOUT this user's items.
    "self_ref_user_boost": 0.25,
    # kind priors (base activation floor per kind)
    "prior_identity": 0.50,
    "prior_hints": 0.50,
    "prior_ctxvar": 0.35,  # small authoritative sets should nearly always fit
    "prior_channel_view": 0.25,
    "prior_task_state": 0.08,
    "prior_session_summary": 0.25,
    "prior_session_fact": 0.18,  # flood-prone → must earn inclusion via L1/L2
    "prior_memory": 0.15,  # flood-prone → lowest floor
    # ── PreAct: Context Density Score + 3-tier routing ───────────────
    # CDS = (relevance × utility) / cost_norm  (react-context-management.md).
    # Utility is the kind's "thought-steering" weight: does the fact change the
    # DECISION (high), or is it merely on-topic (low)? Authoritative/steering
    # kinds (identity, resolved variables, admin hints) push the trajectory;
    # large recall/summary blobs inform but rarely steer. Tunable per bot.
    "utility_identity": 1.2,
    "utility_hints": 1.2,
    "utility_ctxvar": 1.3,  # resolved authoritative variables steer hardest
    "utility_channel_view": 1.0,
    "utility_task_state": 1.0,
    "utility_session_summary": 0.8,
    "utility_session_fact": 0.9,
    "utility_memory": 0.9,
    "utility_default": 1.0,
    "utility_admin_boost": 0.3,  # admin-authored facts are decision-grade
    # cost_norm = max(1, tokens / cost_unit): a node ~cost_unit tokens costs 1.
    # Larger nodes divide CDS down → demoted toward Tier 2 (hint, fetch on ask).
    "cds_cost_unit": 40.0,
    # Tier thresholds on CDS. ≥ inject → Tier 1 (full); ≥ hint → Tier 2
    # (menu_hint + pull tool); else Tier 3 (offload, no pointer).
    "tier_inject_threshold": 0.30,
    "tier_hint_threshold": 0.12,
}


@dataclass
class ContextNode:
    id: str  # stable: f"{kind}:{ref}" e.g. "memory:channel/deploy_day"
    kind: str  # identity|hints|session_summary|session_fact|channel_view|memory|ctxvar
    text: str  # the exact prompt fragment this node contributes
    scope: str | None = None  # memory/ctxvar: bot|user|channel|conversation
    pinned: bool = False  # always selected; bypasses threshold + budget gate
    admin: bool = False  # admin-authored → L1 boost
    stability: str = "slow"  # static|slow|turn — cache-aware composition class
    embed_text: str | None = None  # L2 input (defaults to text)
    # Value-free one-liner for the llm-judge MENU (defaults to text — set it
    # explicitly for kinds whose text/embed_text contains the value).
    menu_hint: str | None = None
    recency_rank: float = 0.0  # 0..1, newest=1 (memory nodes)
    tokens: int = 0  # filled by the builder
    # scoring outputs (filled by build_attention)
    l1: float = 0.0
    l2: float = 0.0
    activation: float = 0.0
    selected: bool = False
    # PreAct outputs (filled by route_tiers): the node's thought-steering
    # value, its density score, and the exposure tier it earned.
    utility: float = 1.0
    cds: float = 0.0
    tier: int = 0  # 0 unset · 1 inject-full · 2 hint+tool · 3 offload
    _order: int = field(default=0, repr=False)  # original composition order


_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Self-referential pronouns (vi+en): the query is about THIS user.
_SELF_REF_TOKENS = {"mình", "tôi", "tớ", "em", "i", "my", "me", "mine"}
_SPREAD_STOP_TOKENS = {
    "task",
    "type",
    "goal",
    "todos",
    "status",
    "last",
    "result",
    "active",
    "paused",
    "completed",
    "cancelled",
    "todo",
    "doing",
    "blocked",
    "done",
    "skipped",
    "failed",
    "freeform",
    "template",
    "scheduler",
    "manual",
    "cron",
    "once",
    "none",
    "null",
}


def _tokens(text: str) -> set[str]:
    """NFC-lowercased word tokens. Diacritics KEPT (meaningful in Vietnamese)."""
    return set(_TOKEN_RE.findall(unicodedata.normalize("NFC", text or "").lower()))


def _rare_tokens(text: str) -> set[str]:
    """Content-bearing tokens for spreading activation. ≥3 chars — Vietnamese
    syllables are short ("họp", "xong"); a 4-char floor silently disabled the
    spread layer for vi (attentionbench finding)."""
    return {t for t in _tokens(text) if len(t) >= 3 and t not in _SPREAD_STOP_TOKENS}


# Process-wide embedding cache: sha256(embed_text) -> vector. Bounded FIFO.
_EMB_CACHE: OrderedDict[str, Any] = OrderedDict()
_EMB_CACHE_MAX = 2048


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _embed_nodes(nodes: list[ContextNode], embed_batch: Callable | None) -> dict[int, Any]:
    """Vectors for each node index, via the process cache + one batch encode."""
    if embed_batch is None:
        return {}
    keys = [_cache_key(n.embed_text or n.text) for n in nodes]
    misses = [
        (i, (nodes[i].embed_text or nodes[i].text))
        for i, k in enumerate(keys)
        if k not in _EMB_CACHE
    ]
    if misses:
        vectors = embed_batch([text for _, text in misses])
        for (i, _), vec in zip(misses, vectors, strict=True):
            _EMB_CACHE[keys[i]] = vec
            while len(_EMB_CACHE) > _EMB_CACHE_MAX:
                _EMB_CACHE.popitem(last=False)
    return {i: _EMB_CACHE[k] for i, k in enumerate(keys) if k in _EMB_CACHE}


def merge_weights(overrides: dict[str, float] | None) -> dict[str, float]:
    """DEFAULT_NODE_WEIGHTS with a sparse per-bot override applied."""
    merged = dict(DEFAULT_NODE_WEIGHTS)
    for key, value in (overrides or {}).items():
        if isinstance(value, (int, float)):
            merged[key] = float(value)
    return merged


def _embed_one(text: str, embed_one: Callable | None) -> Any | None:
    """One cached embedding (content-hash) via an injected ``embed_one(text)->vec``."""
    if embed_one is None or not text:
        return None
    k = _cache_key(text)
    if k not in _EMB_CACHE:
        try:
            _EMB_CACHE[k] = embed_one(text)
        except Exception:
            return None
        while len(_EMB_CACHE) > _EMB_CACHE_MAX:
            _EMB_CACHE.popitem(last=False)
    return _EMB_CACHE.get(k)


def score_relevance(
    query: str,
    q_vec: Any | None,
    text: str,
    *,
    embed_one: Callable | None = None,
    weights: dict[str, float] | None = None,
    prior: float = 0.0,
) -> dict:
    """Generic L1+L2 relevance of ``text`` to the turn ``query`` — the same
    scorer the context attention uses, factored for tool/skill selection (no
    node structure, no spreading). L1 = lexical token overlap; L2 = floor-
    calibrated cosine of the cached text embedding vs ``q_vec`` (skipped when
    ``q_vec`` is None ⇒ activation is L1-only). Deterministic. Returns
    ``{l1, l2, activation}``."""
    w = weights or DEFAULT_NODE_WEIGHTS
    qt = _tokens(query)
    l1 = (len(qt & _tokens(text)) / len(qt)) if qt else 0.0
    l1 = min(1.0, l1)
    l2 = 0.0
    if q_vec is not None:
        vec = _embed_one(text, embed_one)
        if vec is not None:
            try:
                cos = max(0.0, float(vec @ q_vec))
                floor = w.get("l2_floor", 0.0)
                l2 = max(0.0, (cos - floor) / (1.0 - floor)) if floor < 1 else 0.0
            except Exception:
                l2 = 0.0
    activation = w["w_l1"] * l1 + w["w_l2"] * l2 + prior
    return {"l1": l1, "l2": l2, "activation": activation}


def build_attention(
    nodes: list[ContextNode],
    q_text: str,
    q_vec: Any | None,
    *,
    weights: dict[str, float],
    budget_tokens: int,
    min_activation: float,
    embed_batch: Callable | None = None,
) -> tuple[list[ContextNode], dict]:
    """Score (L1+L2) and select (L3) nodes. Deterministic.

    Returns ``(selected nodes in original composition order, trace dict)``.
    ``q_vec`` None ⇒ L2 skipped. ``embed_batch(texts) -> 2D array`` is injected
    (the engine wraps the shared bge-small model); vectors are cached
    process-wide by content hash so stable nodes never re-encode.
    """
    for order, node in enumerate(nodes):
        node._order = order
        node.tokens = est_tokens(node.text)

    # Assembly cap: drop lowest-prior overflow BEFORE embedding (cost bound).
    if len(nodes) > MAX_NODES:
        ranked = sorted(
            nodes,
            key=lambda n: (n.pinned, weights.get(f"prior_{n.kind}", 0.0), -n._order),
            reverse=True,
        )
        nodes = sorted(ranked[:MAX_NODES], key=lambda n: n._order)

    query_tokens = _tokens(q_text)
    self_referential = bool(query_tokens & _SELF_REF_TOKENS)

    vectors = _embed_nodes(nodes, embed_batch) if q_vec is not None else {}

    for i, node in enumerate(nodes):
        # L1 — lexical overlap + structural boosts.
        overlap = 0.0
        if query_tokens:
            node_tokens = _tokens(node.embed_text or node.text)
            overlap = len(query_tokens & node_tokens) / len(query_tokens)
        node._overlap = overlap  # query-driven signal (for spread sourcing)
        l1 = overlap
        if node.admin:
            l1 += weights["admin_boost"]
        if node.scope:
            l1 += weights.get(f"scope_{node.scope}", 0.0)
        if self_referential and node.scope == "user":
            # "what should I hand over?" — the user's own items are on-topic.
            l1 += weights.get("self_ref_user_boost", 0.0)
        if node.kind == "memory":
            l1 += weights["recency_boost"] * max(0.0, min(1.0, node.recency_rank))
        node.l1 = min(1.0, l1)

        # L2 — semantic cosine (normalized embeddings ⇒ dot product),
        # floor-calibrated so the model's unrelated-text baseline scores 0.
        node.l2 = 0.0
        if q_vec is not None and i in vectors:
            try:
                cos = max(0.0, float(vectors[i] @ q_vec))
                floor = weights.get("l2_floor", 0.0)
                node.l2 = max(0.0, (cos - floor) / (1.0 - floor)) if floor < 1 else 0.0
            except Exception:
                node.l2 = 0.0

        prior = weights.get(f"prior_{node.kind}", 0.0)
        node.activation = weights["w_l1"] * node.l1 + weights["w_l2"] * node.l2 + prior

    # L2.5 — spreading activation: the strongest nodes propagate to nodes
    # sharing their rare tokens (2-hop relevance: query→agenda→oauth_status).
    w_spread = weights.get("w_spread", 0.0)
    if w_spread > 0:
        top_n = int(weights.get("spread_top_n", 3))
        source_min = weights.get("spread_source_min", 0.1)
        # A source must be QUERY-LIT LEXICALLY (real token overlap), not just
        # semantically warm — bge-small's vi-vi noise floor (~0.3-0.5 l2 for
        # unrelated texts) otherwise qualifies flooders as sources and they
        # amplify each other's topic cliques (attentionbench hard-set finding:
        # printer_tip flooders at act 1.3+ vs the payment runbook at 0.83).
        # Ranking is query-driven too — structural boosts don't pick sources.
        sources = sorted(
            (n for n in nodes if not n.pinned and getattr(n, "_overlap", 0.0) >= source_min),
            key=lambda n: -(n._overlap + n.l2),
        )[:top_n]
        source_tokens = [(_rare_tokens(s.embed_text or s.text) - query_tokens, s) for s in sources]
        for node in nodes:
            if node.pinned or node in sources:
                continue
            node_tokens = _rare_tokens(node.embed_text or node.text)
            if not node_tokens:
                continue
            best = 0.0
            for src_tokens, src in source_tokens:
                if node.kind == "task_state" and src.kind == "task_state":
                    continue
                if not src_tokens:
                    continue
                # How much of the NODE the source explains (capped denominator
                # so long sources/nodes don't vanish the signal).
                shared = len(node_tokens & src_tokens) / min(len(node_tokens), 8)
                if shared > best:
                    best = shared
            if best > 0:
                node.activation += w_spread * min(1.0, best)

    # L3 — selection: pinned always (tokens still counted); per-kind top-k
    # sparsification for flood-prone kinds; then greedy skip-not-stop fill by
    # activation under the budget.
    used = 0
    for node in nodes:
        if node.pinned:
            node.selected = True
            used += node.tokens
    scored = sorted(
        (n for n in nodes if not n.pinned),
        key=lambda n: (-n.activation, -weights.get(f"prior_{n.kind}", 0.0), n.id),
    )
    kind_counts: dict[str, int] = {}
    for node in scored:
        if node.activation < min_activation:
            continue
        topk = int(weights.get(f"topk_{node.kind}", 0))
        if topk and kind_counts.get(node.kind, 0) >= topk:
            continue  # attention sparsity: only the k best of this kind
        if used + node.tokens <= budget_tokens:
            node.selected = True
            used += node.tokens
            kind_counts[node.kind] = kind_counts.get(node.kind, 0) + 1

    selected = [n for n in nodes if n.selected]
    selected.sort(key=lambda n: n._order)
    trace = {
        "budget_tokens": budget_tokens,
        "min_activation": min_activation,
        "total_tokens": used,
        "nodes": [
            {
                "id": n.id,
                "kind": n.kind,
                "scope": n.scope,
                "stability": n.stability,
                "l1": round(n.l1, 4),
                "l2": round(n.l2, 4),
                "activation": round(n.activation, 4),
                "tokens": n.tokens,
                "selected": n.selected,
                "pinned": n.pinned,
            }
            for n in nodes
        ],
    }
    return selected, trace


# ── PreAct: Context Density Score + 3-tier exposure router ───────────────
#
# The binary selected/dropped model has a failure mode — the *silent drop*: a
# useful fact loses the budget gate by a hair, disappears, and the model never
# learns it existed (so it can't even choose to retrieve it). ``route_tiers``
# replaces that with three exposure tiers (react-context-management.md):
#
#   Tier 1 inject-full  — high CDS, fits budget: the node earns its tokens.
#   Tier 2 hint+tool     — medium/uncertain, OR high-value but too large/over
#                          budget: a one-line ``menu_hint`` + a pull affordance.
#   Tier 3 offload       — low/speculative: nothing in the prompt.
#
# A node that would have been dropped but clears the hint threshold lands in
# Tier 2 — discoverable, not gone. Pinned/admin nodes floor to Tier 1.

TIER_INJECT = 1
TIER_HINT = 2
TIER_OFFLOAD = 3


def node_utility(node: ContextNode, weights: dict[str, float]) -> float:
    """The node's thought-steering weight: how much the fact changes the
    decision (not merely how on-topic it is). Kind-priored, admin-boosted."""
    u = weights.get(f"utility_{node.kind}", weights.get("utility_default", 1.0))
    if node.admin:
        u += weights.get("utility_admin_boost", 0.0)
    return u


def context_density(node: ContextNode, weights: dict[str, float]) -> float:
    """CDS = (relevance × utility) / cost_norm. ``relevance`` is the node's
    activation (L1+L2+prior+spread); ``cost_norm`` divides large nodes down so
    a high-value-but-bulky fact is demoted to a hint, not injected whole."""
    cost_unit = weights.get("cds_cost_unit", 40.0) or 40.0
    cost_norm = max(1.0, (node.tokens or est_tokens(node.text)) / cost_unit)
    return (max(0.0, node.activation) * max(0.0, node.utility)) / cost_norm


def route_tiers(
    nodes: list[ContextNode],
    *,
    weights: dict[str, float],
    budget_tokens: int,
    inject_threshold: float | None = None,
    hint_threshold: float | None = None,
) -> tuple[list[ContextNode], dict]:
    """Assign each node an exposure tier by its CDS against the budget.

    Expects ``nodes`` already scored by :func:`build_attention` (so ``activation``
    and ``tokens`` are set). Mutates ``node.utility``/``node.cds``/``node.tier``
    and returns ``(nodes, trace)``. Deterministic.

    Greedy by CDS: pinned/admin floor to Tier 1; a node with ``CDS ≥ inject``
    that fits the remaining Tier-1 budget injects full, otherwise (if it still
    clears ``hint``) it demotes to Tier 2; ``CDS ≥ hint`` but budget-squeezed →
    Tier 2; below ``hint`` → Tier 3. No node above ``hint`` is silently dropped.
    """
    inject = (
        weights.get("tier_inject_threshold", 0.30) if inject_threshold is None else inject_threshold
    )
    hint = weights.get("tier_hint_threshold", 0.12) if hint_threshold is None else hint_threshold

    for order, node in enumerate(nodes):
        if not node.tokens:
            node.tokens = est_tokens(node.text)
        if not node._order:
            node._order = order
        node.utility = node_utility(node, weights)
        node.cds = context_density(node, weights)

    used = 0
    # Pinned/admin are authoritative — Tier 1 regardless of CDS (tokens counted).
    for node in nodes:
        if node.pinned or node.admin:
            node.tier = TIER_INJECT
            used += node.tokens

    ranked = sorted(
        (n for n in nodes if n.tier == 0),
        key=lambda n: (-n.cds, n._order),
    )
    for node in ranked:
        if node.cds >= inject and used + node.tokens <= budget_tokens:
            node.tier = TIER_INJECT
            used += node.tokens
        elif node.cds >= hint:
            # Medium value, OR high-value-but-over-budget/too-large: keep the
            # pointer, pay for the body only if the model asks.
            node.tier = TIER_HINT
        else:
            node.tier = TIER_OFFLOAD

    trace = {
        "inject_threshold": inject,
        "hint_threshold": hint,
        "budget_tokens": budget_tokens,
        "tier1_tokens": used,
        "tier_counts": {
            "1": sum(1 for n in nodes if n.tier == TIER_INJECT),
            "2": sum(1 for n in nodes if n.tier == TIER_HINT),
            "3": sum(1 for n in nodes if n.tier == TIER_OFFLOAD),
        },
        "nodes": [
            {
                "id": n.id,
                "kind": n.kind,
                "activation": round(n.activation, 4),
                "utility": round(n.utility, 4),
                "cds": round(n.cds, 4),
                "tokens": n.tokens,
                "tier": n.tier,
                "pinned": n.pinned,
                "admin": n.admin,
            }
            for n in nodes
        ],
    }
    return nodes, trace


def _node_menu_hint(node: ContextNode) -> str:
    """The one-liner a Tier-2 node contributes — its ``menu_hint`` (or a
    value-stripped fallback), labeled by id so the scope/key is recoverable."""
    hint = (node.menu_hint or "").strip()
    if not hint:
        # Fall back to the node id + a trimmed first line (never the full value).
        first = " ".join((node.text or "").split())[:80]
        hint = first
    nid = str(node.id or "")
    return f"- {nid} — {hint}" if nid else f"- {hint}"


def render_tiered(
    nodes: list[ContextNode],
    *,
    render_full: Callable[[ContextNode], str],
    hint_header: str = "References (recall to read with the memory/read tools)",
) -> tuple[str, str]:
    """Render routed nodes into ``(tier1_text, tier2_block)``.

    Tier 1 → full text via ``render_full`` (the caller's existing line renderer),
    in composition order. Tier 2 → a hint block of ``menu_hint`` one-liners under
    ``hint_header`` (the pull affordance — discoverable, not dropped). Tier 3 →
    nothing. Nodes with an unset tier (router not run) render full (back-compat).
    """
    ordered = sorted(nodes, key=lambda n: n._order)
    tier1 = [render_full(n) for n in ordered if n.tier in (0, TIER_INJECT)]
    tier1_text = "\n".join(filter(None, tier1)).strip()
    hints = [_node_menu_hint(n) for n in ordered if n.tier == TIER_HINT]
    tier2_block = ""
    if hints:
        tier2_block = f"### {hint_header}\n" + "\n".join(hints)
    return tier1_text, tier2_block
