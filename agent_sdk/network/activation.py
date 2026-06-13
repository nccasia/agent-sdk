"""Lobe network — layered reasoning with context-driven activation (RFC 0015).

Phase 0 PURE CORE: nothing here is wired into the interpreter yet. The module
generalizes the proven context-attention architecture (context_builder.py —
nodes, activations, budgeted selection, uniform trace) from the *node*
granularity to two more granularities:

  lobe   one behavior per module, deterministic activation signals, weighted
         edges to later lobes, a bounded receptive field over the turn-scoped
         Global Context blackboard, and write-back of outputs as new nodes.
  path   a named, labeled subgraph template over the lobes (qna, research,
         task_schedule, …) recognized — never dispatched — from free B1
         signals; recognition biases member lobes, the per-lobe activation
         formula stays primary, and unmatched shapes run as emergent paths.

Layers model the reasoning process itself (instinct → behavior); a layer is
simultaneously an execution stage, a prompt segment, and a cache-stability
class. B0 (gates, answer guard) and B1 (feature extraction) stay core code —
a reflex must never depend on a score — so only B2..B5 hold lobes.

Activation:  a_j = prior_j + Σ_k w_k·signal_k(ctx) + Σ_i edge_{i→j}·a_i
                   + Σ_p path_bias_{p→j}·score_p
where the edge sum ranges ONLY over upstream lobes that activated AND
completed (the lobe-level restatement of the attention layer's query-lit
source discipline — a half-confident lobe must not warm up the expensive
path through its leftovers).

Rules inherited from the attention layer's production lessons:
  1. Pinned bypass first — PINNED_LOBES (cite, filter) activate
     unconditionally on answer paths; no weight/threshold/edge can express
     "skip the ground-or-refuse contract".
  2. Per-lobe threshold — a_j < min_activation_j ⇒ the lobe does not run,
     writes nothing back, contributes nothing downstream.
  3. No speculative cascade — sub-threshold or failed lobes never excite
     downstream; their partial products never join the blackboard.
  4. Forward DAG only — edges target a strictly later (layer, order)
     position; execution order is layer order then declaration order,
     preserving RFC 0005's observable stage ordering.

Pure functions throughout — deterministic given (lobes, ctx, weights), no
clock, no I/O, no LLM. The flat sparse weight dict (`prior_<lobe>`,
`w_<signal>`, `edge_<src>__<dst>`, `path_<name>__<lobe>`, `min_<lobe>`,
`budget_<lobe>`, `budget_<layer>`) mirrors ``context_weights`` and is merged
by the same sparse-override semantics (``merge_lobe_weights``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_sdk.contracts.pins import PINNED_LOBES
from agent_sdk.network.context_builder import ContextNode, build_attention

__all__ = [
    "LAYERS",
    "LAYER_INSTINCT",
    "LAYER_PERCEPTION",
    "LAYER_MEMORY",
    "LAYER_SKILL",
    "LAYER_COGNITION",
    "LAYER_EXPRESSION",
    "PINNED_LOBES",
    "RAW_CHUNK_KINDS",
    "ContextBound",
    "LobeSpec",
    "LobeNode",
    "PathSpec",
    "Blackboard",
    "NetworkResolution",
    "merge_lobe_weights",
    "propagate_nodes",
    "validate_network",
    "recognize_paths",
    "resolve_path",
    "propagate",
]

# ── Layers: the reasoning process, brain-shaped ──────────────────────────────

LAYER_INSTINCT = 0  # B0 — reflexes: gates before, answer_guard after (CORE, never lobes)
LAYER_PERCEPTION = 1  # B1 — deterministic feature extraction (CORE, injects no prompt)
LAYER_MEMORY = 2  # B2 — recall lobes that enrich the blackboard
LAYER_SKILL = 3  # B3 — learned procedure selection
LAYER_COGNITION = 4  # B4 — deliberate behavior: the work
LAYER_EXPRESSION = 5  # B5 — output contract: cite/filter pinned, format

# The grounding output-contract lobes. Not pinned — their activation is driven
# by the resolved path's ``grounds`` flag: live on grounding (KB-answering) paths
# (qna/research), dark on non-grounding paths (onboarding/relational/manage/…).
# The gate in ``propagate()`` makes this weight-immune. The ground-or-refuse
# SAFETY contract is enforced in the interpreter (``enforce_citations``),
# independent of this lobe activation.
OUTPUT_CONTRACT_LOBES = frozenset({"cite", "filter"})

LAYERS: dict[int, str] = {
    LAYER_INSTINCT: "instinct",
    LAYER_PERCEPTION: "perception",
    LAYER_MEMORY: "memory",
    LAYER_SKILL: "skill",
    LAYER_COGNITION: "cognition",
    LAYER_EXPRESSION: "expression",
}

# Lobes may only occupy B2..B5 — instinct and perception are core code.
LOBE_LAYERS = frozenset({LAYER_MEMORY, LAYER_SKILL, LAYER_COGNITION, LAYER_EXPRESSION})

# Compression invariant, made structural (prd.md §10): raw KB chunks may enter
# ONLY research's receptive field and never join the shared pool — nothing
# outside research can ever select one because Blackboard.write_back rejects
# these kinds outright.
RAW_CHUNK_KINDS = frozenset({"kb_chunk", "raw_chunk"})


# ── Specs ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContextBound:
    """A lobe's receptive field over the blackboard.

    Selected at the lobe's OWN execution time over the CURRENT pool via the
    existing ``build_attention`` — one selection mechanism, used per-lobe
    instead of once per turn. Empty ``kinds`` = today's full stage context
    (v1 default — tightening is a tuning step gated by the bounded/efficiency
    bench modes, never a v1 default).
    """

    kinds: tuple[str, ...] = ()  # node kinds visible to this lobe (() = all)
    scopes: tuple[str, ...] = ()  # scope filter (() = all)
    budget_tokens: int = 1600
    weights: dict[str, float] = field(default_factory=dict)  # sparse node-weight overlay
    min_activation: float = 0.22


def _no_signals(_ctx: dict) -> dict[str, float]:
    return {}


@dataclass(frozen=True)
class LobeSpec:
    """One behavior per module — a registry row, never an interpreter branch.

    ``signals`` is deterministic and free: lexical cues, session/task state,
    policy flags; it may read q_vec and trace.attention — never an LLM call.
    ``edges`` target strictly later (layer, order) positions (>0 excite,
    <0 inhibit). ``order`` is the intra-layer execution rank (declaration
    order in the default registry).
    """

    id: str
    behavior: str  # "recall", "rewrite", "route", "decompose", …
    layer: int
    prior: float = 0.0
    pinned: bool = False  # PRD-invariant lobes bypass threshold entirely
    attends: ContextBound = field(default_factory=ContextBound)
    signals: Callable[[dict], dict[str, float]] = _no_signals
    # Per-signal default weights (overridable via the flat surface as
    # ``w_<signal>``). A signal absent here defaults to weight 1.0.
    signal_weights: dict[str, float] = field(default_factory=dict)
    edges: dict[str, float] = field(default_factory=dict)  # lobe_id -> weight
    writes: tuple[str, ...] = ()  # node kinds this lobe writes back
    min_activation: float = 0.5
    order: int = 0
    # Phase 2+ opt-in: when True, the layered prompt composer routes this
    # lobe's slice of the system prompt through ``LOBE.build_context(ctx)``
    # (the per-lobe context builder) instead of the inlined legacy block.
    # Default False — every built-in lobe keeps the centralized flow active
    # until Phase 4 explicitly opts it in (per-lobe opt-in, never blanket).
    build_context: bool = False

    def __post_init__(self) -> None:
        if self.layer not in LOBE_LAYERS:
            raise ValueError(
                f"lobe {self.id!r}: layer {self.layer} is core machinery, not a lobe layer "
                f"(lobes live in B2..B5)"
            )


@dataclass(frozen=True)
class PathSpec:
    """A well-known reasoning path — a labeled subgraph template over lobes.

    ``recognizer`` scores the path from free B1 signals (0..1). Recognition
    BIASES member lobes (``bias`` defaults, overridable as
    ``path_<name>__<lobe>``); it never hard-gates — wrongly-biased lobes
    still need their own signals to clear threshold, and unmatched shapes
    resolve to an emergent path.

    Phase 7+ — ``stage_names`` declares the **progressive execution
    sequence** the path runs (the stage axis). Each stage owns its own
    system prompt composition (lobe axis) and agentic loop. The same
    mental model as the lobe axis (predefined state, signal-gated
    activation) lives in a sibling namespace.
    """

    name: str
    members: tuple[str, ...]
    recognizer: Callable[[dict], float]
    bias: dict[str, float] = field(default_factory=dict)  # lobe_id -> default bias
    threshold: float = 0.5  # recognition floor — below it the path stays dark
    # Phase 7+ — the per-path stage sequence. Empty tuple means the path
    # has no stages (the activated lobes' own writes are the answer).
    # The default paths all declare their sequences (qna → [synthesize];
    # research → [plan, research, synthesize, cite, filter]; etc.).
    stage_names: tuple[str, ...] = ()
    # Whether this path produces a *grounded* (KB-answering) reply. True for
    # every answer path (the default). False marks a non-grounding path — e.g.
    # onboarding/steward, which configures the bot via admin.* tools and never
    # retrieves a KB — on which the output-contract lobes ``cite``/``filter``
    # do not activate (nothing to cite). This NEVER relaxes safety: the
    # ground-or-refuse contract is enforced in the interpreter
    # (``enforce_citations`` keyed on whether retrieval ran), independent of
    # lobe activation. ``grounds`` is structural code, not a tuning weight, so
    # the "no weight can disable cite/filter" invariant (PINNED_LOBES) holds.
    grounds: bool = True


# ── LobeNode: the per-lobe state machine (Phase 4+) ──────────────────────────


@dataclass(frozen=True)
class LobeNode:
    """One opt-in node inside a lobe's state machine.

    A lobe is a coarse-grained behavior (skill_activate, task_state, ...). A
    LobeNode is the FINE-grained unit — the small, individually-toggleable
    behavior that the lobe composes. Examples for ``skill_activate``:

    - ``skill:list`` — render the visible skill list (1 node per skill)
    - ``skill:in_use`` — "you have N skills in use" marker
    - ``skill.read:hint`` — progressive-disclosure hint when an active
      skill declares a ``read`` directive (RFC 0013)

    Each node has its own enable conditions (``signals`` + ``prior`` +
    ``min_activation``) so the lobe's contribution to the system prompt
    is shaped by the current turn state and conversation context — the
    "LLM's internal thoughts formed by the current context" the design
    calls for. Per-bot customization:

    - ``flow_lobe_weights["disable_<lobe>_<node>"]`` — toggle a node off
      (``1.0`` disables, ``0`` enables back)
    - ``flow_lobe_weights["prior_<lobe>_<node>"]`` — override the prior
    - ``flow_lobe_weights["min_<lobe>_<node>"]`` — override the threshold

    The node's ``produce`` returns ``ContextNode``s (the data path) and
    ``prompt`` returns ``PromptContribution``s (the prompt-text path).
    Both are pure functions of the ``TurnContext`` (no LLM, no I/O).
    Default impls return empty — subclasses (or ``node_factory``-style
    closures) override the behavior.
    """

    id: str
    lobe_id: str
    layer: int
    stability: str = "stable"  # "stable" / "slow" / "volatile"
    prior: float = 0.0
    signals: Callable[[dict], dict[str, float]] = _no_signals
    signal_weights: dict[str, float] = field(default_factory=dict)
    min_activation: float = 0.5
    order: int = 0
    description: str = ""
    enabled_default: bool = True
    # The data path: what ContextNodes the node contributes. Default is
    # a no-op closure; subclasses override.
    produce: Callable[[Any], list] = field(default=lambda _ctx: [])
    # The prompt-text path: what PromptContributions the node contributes.
    # Default is a no-op closure; subclasses override.
    prompt: Callable[[Any], list] = field(default=lambda _ctx: [])

    def __post_init__(self) -> None:
        if self.layer not in LOBE_LAYERS:
            raise ValueError(
                f"node {self.id!r}: layer {self.layer} is core machinery, not a lobe layer "
                f"(nodes live in B2..B5)"
            )
        if self.stability not in {"stable", "slow", "volatile"}:
            raise ValueError(
                f"node {self.id!r}: stability {self.stability!r} must be stable/slow/volatile"
            )


def propagate_nodes(
    nodes: list[LobeNode],
    ctx: dict,
    *,
    weights: dict[str, float],
) -> list[dict]:
    """Resolve which nodes activate this turn (Phase 4+).

    For each candidate node:

    - ``prior_j + Σ_k w_k · signal_k(ctx)`` — deterministic, free
    - ``a_j < min_activation_j`` ⇒ node stays dark, contributes nothing
    - ``flow_lobe_weights["disable_<lobe>_<node>"] = 1`` ⇒ explicit off
    - ``flow_lobe_weights["prior_<lobe>_<node>"] = float`` ⇒ prior override
    - ``flow_lobe_weights["min_<lobe>_<node>"] = float`` ⇒ threshold override

    Returns one entry per candidate node, in ``(layer, order, id)`` order,
    shaped like ``trace.lobes[i]`` so the same offline tooling reads both::

        {
          "id": "skill:list",
          "lobe_id": "skill_activate",
          "layer": 3, "stability": "stable",
          "signals": {"skills_declared": 1.0},
          "activation": 1.0, "activated": True,
          "disabled_by_overlay": False,
          "reason": "skills_declared",
        }

    The activation is per-node; per-lobe activation stays the dispatch
    surface (RFC 0015). The ``lobe_id`` field is the namespacing anchor
    for the flat weight surface.
    """
    out: list[dict] = []
    for node in sorted(nodes, key=lambda n: (n.layer, n.order, n.id)):
        # Per-node overrides (lobe-prefixed for namespacing).
        disable_key = f"disable_{node.lobe_id}_{node.id}"
        prior_key = f"prior_{node.lobe_id}_{node.id}"
        min_key = f"min_{node.lobe_id}_{node.id}"
        disabled_by_overlay = bool(weights.get(disable_key, 0.0))
        prior = float(weights.get(prior_key, node.prior))
        min_act = float(weights.get(min_key, node.min_activation))
        # Signal evaluation — the node's ``signal_weights`` is the source
        # of truth for which signals participate. Earlier versions read
        # EVERY key in the signals dict, defaulting missing weights to
        # 1.0 — that over-fired cross-node when the lobe's signal vocab
        # (e.g. ``has_in_progress_task`` AND ``has_any_task``) is
        # wider than the node's own weight surface.
        raw_signals = node.signals(ctx) or {}
        signal_weight = node.signal_weights or {}
        signal_sum = 0.0
        for sig_name, sig_w in signal_weight.items():
            if sig_name in raw_signals:
                signal_sum += float(sig_w) * float(raw_signals[sig_name])
        a = prior + signal_sum
        activated = (not disabled_by_overlay) and (a >= min_act)
        if disabled_by_overlay:
            reason = "disabled_by_overlay"
        elif a < min_act:
            reason = "below_threshold"
        else:
            # Reason is a list of the signals that fired (those the
            # node's signal_weights declared AND the lobe's signals
            # function returned a positive value for).
            fired = [k for k in signal_weight if float(raw_signals.get(k, 0.0)) > 0.0]
            reason = "+".join(fired) if fired else "prior_only"
        out.append(
            {
                "id": node.id,
                "lobe_id": node.lobe_id,
                "layer": node.layer,
                "stability": node.stability,
                "description": node.description,
                "signals": {k: round(v, 4) for k, v in (raw_signals or {}).items()},
                "prior": round(prior, 4),
                "signal_weight_sum": round(signal_sum, 4),
                "activation": round(a, 4),
                "min_activation": round(min_act, 4),
                "activated": activated,
                "disabled_by_overlay": disabled_by_overlay,
                "reason": reason,
            }
        )
    return out


# ── Weights ──────────────────────────────────────────────────────────────────


def merge_lobe_weights(
    defaults: dict[str, float], overrides: dict[str, float] | None
) -> dict[str, float]:
    """Defaults with a sparse per-bot override applied — same semantics as
    ``context_builder.merge_weights`` (the node surface); the two surfaces
    stay independent so a tuning mistake in one cannot disturb the other."""
    merged = dict(defaults)
    for key, value in (overrides or {}).items():
        if isinstance(value, (int, float)):
            merged[key] = float(value)
    return merged


# ── Network validation ───────────────────────────────────────────────────────


def validate_network(lobes: list[LobeSpec]) -> None:
    """Reject malformed networks at registration time.

    Forward DAG: every edge targets an existing lobe at a strictly later
    (layer, order) position. Pinned protection: no negative (inhibitory)
    edge may target a pinned lobe — the save-time policy validator is
    defense-in-depth at the schema layer; this is the structural guarantee.
    """
    by_id = {lobe.id: lobe for lobe in lobes}
    if len(by_id) != len(lobes):
        seen: set[str] = set()
        dup = next(lobe.id for lobe in lobes if lobe.id in seen or seen.add(lobe.id))
        raise ValueError(f"duplicate lobe id {dup!r}")
    for lobe in lobes:
        for target_id, weight in lobe.edges.items():
            target = by_id.get(target_id)
            if target is None:
                raise ValueError(f"lobe {lobe.id!r}: edge to unknown lobe {target_id!r}")
            if (target.layer, target.order) <= (lobe.layer, lobe.order):
                raise ValueError(
                    f"lobe {lobe.id!r}: edge to {target_id!r} is not forward "
                    f"(({lobe.layer},{lobe.order}) → ({target.layer},{target.order})) — "
                    f"the network is a forward DAG"
                )
            if weight < 0 and target.pinned:
                raise ValueError(
                    f"lobe {lobe.id!r}: inhibitory edge to pinned lobe {target_id!r} "
                    f"(a weight can never express 'skip the ground-or-refuse contract')"
                )


# ── Path recognition (B1 — free, deterministic, per turn) ────────────────────


def recognize_paths(ctx: dict, paths: list[PathSpec]) -> dict[str, float]:
    """Score every named path from the turn's free signals. Scores are PATH
    PRIORS, not a routing decision — each turn resolves its own."""
    return {p.name: round(max(0.0, min(1.0, float(p.recognizer(ctx)))), 4) for p in paths}


def resolve_path(scores: dict[str, float], paths: list[PathSpec]) -> dict:
    """The turn's resolved reasoning path → the ``trace.path`` shape.

    ``name`` is "emergent" when no named path cleared its recognition
    threshold — the activated lobe set in trace.lobes then IS the path's
    definition, which is what makes emergent shapes promotable to named
    paths from traces alone.
    """
    thresholds = {p.name: p.threshold for p in paths}
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    cleared = [(name, score) for name, score in ranked if score >= thresholds.get(name, 0.5)]
    runner_up = None
    if cleared:
        name, score = cleared[0]
        rest = [(n, s) for n, s in ranked if n != name]
        if rest:
            runner_up = {"name": rest[0][0], "score": rest[0][1]}
        return {"name": name, "score": score, "runner_up": runner_up, "emergent": False}
    if ranked:
        runner_up = {"name": ranked[0][0], "score": ranked[0][1]}
    return {"name": "emergent", "score": 0.0, "runner_up": runner_up, "emergent": True}


# ── The Global Context blackboard ────────────────────────────────────────────


class Blackboard:
    """Turn-scoped node pool replacing one-pass enrichment.

    Opens with the B0/B1 products (identity, hints, channel view, query
    nodes) and grows as lobes run. Each node carries provenance
    (``produced_by``); only nodes written by lobes that activated AND
    completed are eligible selection sources. Raw KB chunks never join the
    pool (write_back rejects RAW_CHUNK_KINDS) — the compression invariant
    enforced by the data flow instead of by review.

    Phase 3+ — per-layer budget enforcement. The optional ``layer_budgets``
    constructor arg is the optimization surface: ``{LAYER_MEMORY: 600,
    LAYER_SKILL: 400, LAYER_COGNITION: 800, LAYER_EXPRESSION: 0}`` (defaults
    from policy.context_budget_tokens / flow_layer_budgets). When set and
    ``write_back(layer=<int>, ...)`` is called, the writer trims the
    contributed nodes via ``select_for`` to fit the per-layer cap. Pinned
    lobes (``PINNED_LOBES``) skip the trim — the ground-or-refuse contract
    is never a budget decision. Strictly opt-in: when ``layer_budgets`` is
    None (default), the new kwargs are no-ops and the legacy contract
    (write_back returns ids, that's it) is byte-identical.
    """

    def __init__(
        self,
        nodes: list[ContextNode] | None = None,
        *,
        layer_budgets: dict[int, int] | None = None,
    ):
        self._nodes: list[ContextNode] = []
        self._provenance: dict[str, str] = {}
        self._layer_budgets: dict[int, int] = dict(layer_budgets or {})
        # Per-lobe write-back metadata for the trace surface: budget_hint
        # (the per-layer cap that applied), tokens_in (sum of contributed
        # node tokens BEFORE trim), tokens_after (sum AFTER trim). Read by
        # the interpreter's _attach_flow_trace to populate trace.lobes[i].
        self._write_meta: dict[str, dict[str, Any]] = {}
        for node in nodes or []:
            self._add(node, produced_by="turn")

    def _add(self, node: ContextNode, *, produced_by: str) -> None:
        if node.kind in RAW_CHUNK_KINDS:
            raise ValueError(
                f"node {node.id!r}: kind {node.kind!r} never joins the shared pool — "
                f"raw chunks are confined to research's receptive field (prd.md §10)"
            )
        self._nodes.append(node)
        self._provenance[node.id] = produced_by

    @staticmethod
    def _node_tokens(node: ContextNode) -> int:
        """Best-effort token estimate — reuses ContextNode.tokens when set,
        else a length/4 fallback. Used only for budget enforcement; never
        affects node identity or write-back ordering."""
        explicit = getattr(node, "tokens", None)
        if isinstance(explicit, (int, float)) and explicit > 0:
            return int(explicit)
        text = str(getattr(node, "text", "") or "")
        return max(1, len(text) // 4)

    def write_back(
        self,
        lobe_id: str,
        nodes: list[ContextNode],
        *,
        completed: bool = True,
        layer: int | None = None,
        q_text: str | None = None,
        q_vec: Any | None = None,
        pinned: bool = False,
    ) -> list[str]:
        """Convert a completed lobe's output into new context nodes.

        ``completed=False`` (the lobe failed after activation) drops the
        nodes entirely — no speculative cascade, no leftovers warming up
        downstream selection. Returns the ids actually written.

        Phase 3+ — when ``layer`` is set AND the Blackboard has a cap for
        that layer AND the lobe is not pinned, the writer enforces the
        per-layer token budget: it sorts contributed nodes by the existing
        attention scoring (lexical overlap with ``q_text``) and keeps the
        highest-scoring nodes until the cap is met. Trimmed nodes are NOT
        added to the pool. The metadata (``tokens_in``, ``tokens_after``,
        ``budget_hint``) is stored on ``self._write_meta[lobe_id]`` for
        the trace surface.
        """
        if not completed:
            self._write_meta[lobe_id] = {
                "budget_hint": 0,
                "tokens_in": 0,
                "tokens_after": 0,
                "trimmed": 0,
            }
            return []
        candidates = list(nodes)
        tokens_in = sum(self._node_tokens(n) for n in candidates)
        budget_hint = 0
        tokens_after = tokens_in
        trimmed = 0
        if (
            layer is not None
            and not pinned
            and self._layer_budgets
            and (cap := self._layer_budgets.get(layer)) is not None
            and cap > 0
        ):
            budget_hint = int(cap)
            if tokens_in > cap:
                # Score by lexical overlap (free, deterministic) — pinned
                # nodes (caller marked via ``pinned=True``) and the highest
                # tokens-first nodes survive. No LLM call: same primitive
                # build_attention uses for L1 scoring.
                query = str(q_text or "").lower()

                def _score(n: ContextNode) -> tuple[int, int, int]:
                    text = str(getattr(n, "text", "") or "").lower()
                    overlap = sum(1 for tok in query.split() if tok and tok in text) if query else 0
                    return (-overlap, -self._node_tokens(n), 0)

                # ``_score`` puts the best items (most overlap, then
                # smallest) at the FRONT of the sorted list. Walk the list
                # greedily and stop at the first overflow — everything past
                # that point is dropped. ``kept[:cutoff]`` survives;
                # ``kept[cutoff:]`` is the trimmed tail.
                kept = sorted(candidates, key=_score)
                running = 0
                cutoff = len(kept)
                for i, n in enumerate(kept):
                    if running + self._node_tokens(n) > cap:
                        cutoff = i
                        break
                    running += self._node_tokens(n)
                trimmed = len(kept) - cutoff
                candidates = kept[:cutoff]
                tokens_after = running
        written: list[str] = []
        for node in candidates:
            self._add(node, produced_by=lobe_id)
            written.append(node.id)
        self._write_meta[lobe_id] = {
            "budget_hint": budget_hint,
            "tokens_in": int(tokens_in),
            "tokens_after": int(tokens_after),
            "trimmed": int(trimmed),
        }
        return written

    def get_write_meta(self, lobe_id: str) -> dict[str, Any]:
        """The per-lobe write-back trace (Phase 3+). Empty dict if the lobe
        hasn't written back yet. The interpreter reads this in
        ``_attach_flow_trace`` to populate ``trace.lobes[i].budget_hint``,
        ``tokens_in``, ``tokens_after``."""
        return dict(self._write_meta.get(lobe_id) or {})

    def layer_budgets(self) -> dict[int, int]:
        """The active per-layer caps (snapshot). Empty dict when unset."""
        return dict(self._layer_budgets)

    def provenance(self, node_id: str) -> str | None:
        return self._provenance.get(node_id)

    @property
    def nodes(self) -> list[ContextNode]:
        return list(self._nodes)

    def visible_to(self, bound: ContextBound) -> list[ContextNode]:
        """The slice of the CURRENT pool inside a lobe's receptive field."""
        out = []
        for node in self._nodes:
            if bound.kinds and node.kind not in bound.kinds:
                continue
            if bound.scopes and node.scope is not None and node.scope not in bound.scopes:
                continue
            out.append(node)
        return out

    def select_for(
        self,
        lobe: LobeSpec,
        q_text: str,
        q_vec: Any | None,
        *,
        node_weights: dict[str, float],
        embed_batch: Callable | None = None,
    ) -> tuple[list[ContextNode], dict]:
        """A lobe's bounded slice, selected at its own execution time over
        the current pool — ``build_attention`` reused per-lobe (G3)."""
        from agent_sdk.network.context_builder import merge_weights as _merge_node_weights

        bound = lobe.attends
        visible = self.visible_to(bound)
        # node_weights and the lobe's own overlay are both sparse overrides on
        # DEFAULT_NODE_WEIGHTS — build_attention expects the full surface.
        weights = _merge_node_weights(node_weights)
        for key, value in bound.weights.items():
            if isinstance(value, (int, float)):
                weights[key] = float(value)
        return build_attention(
            visible,
            q_text,
            q_vec,
            weights=weights,
            budget_tokens=bound.budget_tokens,
            min_activation=bound.min_activation,
            embed_batch=embed_batch,
        )


# ── Propagation: activation and cascade ──────────────────────────────────────


@dataclass
class NetworkResolution:
    """The resolved network for one turn — trace-shaped, JSON-safe."""

    lobes: list[dict]  # one entry per candidate lobe (the trace.lobes shape)
    path: dict  # the trace.path shape
    activated: list[str]  # activated lobe ids in execution order

    @property
    def by_id(self) -> dict[str, dict]:
        return {entry["id"]: entry for entry in self.lobes}


def _execution_order(lobes: list[LobeSpec]) -> list[LobeSpec]:
    return sorted(lobes, key=lambda lobe: (lobe.layer, lobe.order, lobe.id))


def propagate(
    lobes: list[LobeSpec],
    ctx: dict,
    *,
    weights: dict[str, float],
    paths: list[PathSpec] | None = None,
    min_activation: float = 0.0,
    failed: set[str] | None = None,
) -> NetworkResolution:
    """Resolve the turn's activated subgraph. Pure and deterministic.

    ``ctx`` is the B1 signal substrate (a plain dict — query text, session/
    task state features, policy flags, upstream write-back products such as
    the classify route). ``weights`` is the MERGED flat surface
    (``merge_lobe_weights(DEFAULT_LOBE_WEIGHTS, policy.flow_lobe_weights)``).
    ``min_activation`` is the policy-level floor (``flow_min_activation``);
    per-lobe thresholds (``min_<lobe>`` / spec default) sit on top.
    ``failed`` marks lobes that activated but did not complete — they excite
    nothing downstream (used by the staged runtime; the bench passes it to
    prove no-speculative-cascade).
    """
    validate_network(lobes)
    failed = failed or set()
    paths = paths or []

    path_scores = recognize_paths(ctx, paths)
    path_trace = resolve_path(path_scores, paths)
    # The winning path's grounding flag, gating the output-contract lobes below.
    # Named paths carry it (qna/research True; the rest False). An UNRECOGNIZED
    # shape (emergent / no path) is treated as non-grounding — we only run the
    # grounding lobes on a path we recognise as KB-answering. Safety is
    # unaffected: enforce_citations (interpreter) still fires on any turn that
    # actually retrieves, independent of this.
    path_grounds = next((s.grounds for s in paths if s.name == path_trace.get("name")), False)
    # Biases from every RECOGNIZED path (score ≥ its threshold) — a runner-up
    # that also cleared recognition still nudges its members; biasing, never
    # gating, keeps the network able to compose novel pipelines.
    bias: dict[str, float] = {}
    bias_reason: dict[str, str] = {}
    for spec in paths:
        score = path_scores.get(spec.name, 0.0)
        if score < spec.threshold:
            continue
        for member in spec.members:
            default = spec.bias.get(member, 0.0)
            weight = weights.get(f"path_{spec.name}__{member}", default)
            if weight == 0.0:
                continue
            contribution = weight * score
            bias[member] = bias.get(member, 0.0) + contribution
            if contribution > 0:
                bias_reason.setdefault(member, f"path:{spec.name}")

    activation: dict[str, float] = {}
    completed: dict[str, bool] = {}
    entries: list[dict] = []
    activated_order: list[str] = []

    for lobe in _execution_order(lobes):
        raw_signals = {k: float(v) for k, v in (lobe.signals(ctx) or {}).items()}
        signal_sum = 0.0
        for name, value in raw_signals.items():
            w = weights.get(f"w_{name}", lobe.signal_weights.get(name, 1.0))
            signal_sum += w * value

        in_edges: dict[str, float] = {}
        edge_sum = 0.0
        for src in lobes:
            edge = weights.get(f"edge_{src.id}__{lobe.id}", src.edges.get(lobe.id, 0.0))
            if edge == 0.0:
                continue
            # Propagation ranges ONLY over upstream lobes that activated AND
            # completed — source discipline at the lobe granularity.
            if not completed.get(src.id):
                continue
            contribution = edge * activation[src.id]
            in_edges[src.id] = round(contribution, 4)
            edge_sum += contribution

        prior = weights.get(f"prior_{lobe.id}", lobe.prior)
        a = prior + signal_sum + edge_sum + bias.get(lobe.id, 0.0)
        threshold = max(min_activation, weights.get(f"min_{lobe.id}", lobe.min_activation))

        if lobe.id in OUTPUT_CONTRACT_LOBES:
            # Grounding output-contract lobes (cite/filter): activation is driven
            # by the resolved path's grounding signal (free, known at dispatch) —
            # NOT an unconditional pin and NOT their own prior/signals/weights.
            # Active on grounding paths (qna/research), dark elsewhere. This stays
            # weight-immune: a hostile flow_lobe_weight cannot flip them on a
            # grounding path. Safety (ground-or-refuse) is enforced separately in
            # the interpreter (enforce_citations), independent of this activation.
            activated = path_grounds
            reason = "grounding_path" if path_grounds else "non_grounding_path"
        elif lobe.pinned:
            activated, reason = True, "pinned:invariant"
        elif a >= threshold:
            activated = True
            # Own signals first (the formula stays primary), then path bias,
            # then edges, then prior — always human-readable.
            reason = _activation_reason(raw_signals, in_edges, bias_reason.get(lobe.id))
        else:
            activated, reason = False, "below_threshold"

        activation[lobe.id] = a if activated else 0.0
        completed[lobe.id] = activated and lobe.id not in failed
        if activated:
            activated_order.append(lobe.id)

        entries.append(
            {
                "id": lobe.id,
                "layer": lobe.layer,
                "behavior": lobe.behavior,
                "signals": {k: round(v, 4) for k, v in raw_signals.items()},
                "in_edges": in_edges,
                "activation": round(a, 4),
                "activated": activated,
                "pinned": lobe.pinned,
                "reason": reason if lobe.id not in failed else "failed",
            }
        )

    return NetworkResolution(lobes=entries, path=path_trace, activated=activated_order)


def _activation_reason(
    signals: dict[str, float], in_edges: dict[str, float], path_reason: str | None = None
) -> str:
    """Human-readable activation reason — mirrors the attention node trace so
    the same offline tooling reads both."""
    lit = [name for name, value in signals.items() if value > 0]
    if lit:
        return "+".join(sorted(lit))
    if path_reason:
        return path_reason
    if in_edges:
        strongest = max(in_edges, key=lambda k: in_edges[k])
        return f"edge:{strongest}"
    return "prior"
