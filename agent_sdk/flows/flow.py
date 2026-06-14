"""Flows — the progressive-state axis (Phase 7+ second axis).

A **flow** is a complete, named pipeline: an ordered sequence of
**flow steps** (formerly called "stages" in Phase 7a-d). The path picks
the flow; the steps run in order, each composing its own system prompt
from the lobes the step consults.

The two axes are **independent data models**:

- **Lobe axis** (``agent_core.lobes``) — passive context workers. Each
  lobe has a state machine of opt-in nodes (``LobeNode``) that emit
  ``ContextNode``s for the system prompt + blackboard. Lobes are
  unchanged by this refactor.

- **Flow axis** (``agent_core.flows``, this module) — progressive
  pipeline orchestrator. A flow step has its own system prompt
  composition (the lobe axis) and its own agentic loop (``none`` /
  ``single`` / ``agentic``). Steps declare a *slice* of lobes — the
  lobes they consult for context.

A turn is the cross product of (lobe axis: which context chunks fire)
× (flow axis: which progressive execution step is currently running).
The flow is the **folder**; the steps are its **contents**.

Per-bot customization for flows uses a separate flat-weight namespacing:
``flow_disable_<flow>``, ``flow_prior_<step>``, etc. — sibling of the
lobe axis's ``disable_<lobe>_<node>`` keys, dispatched by the
``flow_`` prefix.

Testable separately: lobe axes have their own test surface
(``tests/test_lobe_*``); flow axes have their own (this module's
companion ``tests/test_flows.py``). The two axes are
**independently optimizable** — a new flow doesn't require touching
any lobe; a new lobe doesn't require touching any flow.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


def _no_signals(_ctx: dict) -> dict[str, float]:
    return {}


# RFC 0017 — a stage's ``type`` is its running model: the friendly name of the
# internal ``loop`` field. ReAct = the agentic tool loop; simple = one call;
# map = parallel fan-out; none = pure prompt build (no LLM).
_RUNNING_MODEL = {"agentic": "react", "single": "simple", "map": "map", "none": "none"}


__all__ = [
    "FlowStep",
    "FlowStepNode",
    "Flow",
    "propagate_flow_steps",
    "step_signal_node",
    "BaseFlow",
    "FlowStepResult",
]


@dataclass(frozen=True)
class FlowStep:
    """One progressive block of a flow's execution (the flow-axis unit).

    A step owns:
    - **Lobe slice** — which lobes' state-machine nodes contribute to
      this step's system prompt (the cross-axis bridge).
    - **Agentic loop** — ``none`` (pure prompt build), ``single`` (one
      LLM call), ``agentic`` (ReAct tool loop).
    - **Tools** — the tool ids available in the agentic loop.
    - **Per-step state** — optional predefined states (the step's own
      signal-gated state machine, sibling of ``LobeNode``).
    - **Output** — the step's result is consumed by the next step in
      the flow; the final step's output IS the response.

    Same mental model as ``Stage`` (Phase 7a-d) — the rename to
    ``FlowStep`` makes the two-axis separation explicit. The runtime
    hooks (``loop`` + ``tools`` + per-step signals) are unchanged.
    """

    # The stage's NAME is its known reasoning STATE — what it does, with its own
    # purpose/prompt/lobes (plan / research / synthesize / cite / filter /
    # format / …). These are general agent building blocks; the RAG `research`
    # flow is just one known path that happens to chain plan→research→…→filter.
    name: str
    lobes: tuple[str, ...] = ()  # which lobes' LobeNodes contribute
    # The RUNNING MODEL — *how* the stage executes (its ``type``, surfaced by the
    # ``type`` property below): none / single / agentic / map. The ``stage()``
    # builder defaults to ``agentic`` (stages are mostly ReAct); the raw
    # dataclass default stays ``single`` for low-level/back-compat constructions.
    loop: str = "single"
    tools: tuple[str, ...] = ()
    description: str = ""
    # Per-step state machine (default empty; opt in per-step). Signal-
    # gated, namespaced under ``flow_<step>__<node>`` in the flat
    # weight surface.
    state_nodes: tuple[FlowStepNode, ...] = ()
    # ``loop="map"`` fan-out: the turn-scratchpad list key this step maps over.
    # The model (or an earlier step) writes a work-list there (e.g. the plan
    # stage → ``sub_questions``); the step then runs one agentic sub-run per
    # item in parallel (bounded by the research semaphore + ``fanout_max``).
    # Empty/missing list ⇒ degrades to a single agentic run (never loses the
    # turn). Ignored unless ``loop == "map"``.
    fanout_key: str = ""
    # Fan-out shape (``loop="map"`` only). Defaults reproduce today's behavior:
    # sequential state-carry over a shared evidence pool. ``fanout_parallel`` runs
    # workers concurrently (gather, bounded by ``fanout_max``); ``fanout_isolated``
    # gives each worker a fresh evidence pool (only its memo returns). See doc 12.
    fanout_parallel: bool = False
    fanout_max: int = 40
    fanout_isolated: bool = False

    # ── RFC 0017: first-class signal (the merged FlowStepNode common case) ────
    # A step's own activation signal — same shape as a lobe's. The default
    # (``_no_signals`` + ``min_activation=0.0``) is an always-on structural
    # step, byte-identical to today. ``state_nodes`` above stays for the rare
    # multi-signal step; a single signal lives here on the step itself.
    signals: Callable[[dict], dict[str, float]] = _no_signals
    signal_weights: dict[str, float] = field(default_factory=dict)
    prior: float = 0.0
    min_activation: float = 0.0  # 0 ⇒ always-on (today's structural default)

    # ── RFC 0017: default inference config (the engine's source of truth) ─────
    # Was ``policy.stages[name]``. ``None`` ⇒ the runner falls back to the
    # policy's ``stage_config`` override, then to ``policy.stages[name]``
    # (Phase-1 back-compat), then to the engine settings default — so a default
    # flow that leaves these unset behaves exactly as before.
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    hops: int | None = None
    system_prompt: str | None = None

    def __post_init__(self) -> None:
        if self.loop not in {"none", "single", "agentic", "map"}:
            raise ValueError(
                f"flow step {self.name!r}: loop {self.loop!r} must be one of "
                "none/single/agentic/map"
            )
        if self.loop == "map" and not self.fanout_key:
            raise ValueError(f"flow step {self.name!r}: loop='map' requires a fanout_key")

    @property
    def type(self) -> str:
        """RFC 0017 — the stage's TYPE = its running model (how it executes),
        the friendly name of ``loop``: react | simple | map | none. Orthogonal
        to ``name`` (the known reasoning state). ``loop`` stays the field the
        runner switches on; ``type`` is its inspectable/declarative view."""
        return _RUNNING_MODEL.get(self.loop, self.loop)


@dataclass(frozen=True)
class FlowStepNode:
    """One opt-in node inside a flow step's state machine (Phase 7+).

    Same mental model as ``LobeNode`` — predefined state, signal-gated
    activation, per-bot disable/prior/min — but the output is
    execution-shaped (a prompt contribution, a tool injection, a flag)
    rather than data-shaped (a ``ContextNode``). Namespacing:
    ``flow_disable_<flow>_<step>_<node>``,
    ``flow_prior_<flow>_<step>_<node>``, etc.
    """

    id: str
    flow: str
    step: str
    prior: float = 0.0
    signals: Callable[[dict], dict[str, float]] = _no_signals
    signal_weights: dict[str, float] = field(default_factory=dict)
    min_activation: float = 0.5
    order: int = 0
    description: str = ""
    enabled_default: bool = True
    produce: Callable[[Any], list] = field(default=lambda _ctx: [])
    prompt: Callable[[Any], list] = field(default=lambda _ctx: [])

    def __post_init__(self) -> None:
        if self.min_activation < 0:
            raise ValueError(
                f"flow step node {self.id!r}: min_activation {self.min_activation} must be >= 0"
            )


@dataclass(frozen=True)
class Flow:
    """A complete, named pipeline (the flow axis's outermost unit).

    A flow is an ordered sequence of ``FlowStep``s plus the per-bot
    customization surface. A turn that resolves to a flow runs its
    steps in order, each step composing its system prompt from the
    lobes in its slice and running its own agentic loop.

    Example::

        Flow(
            name="qna",
            steps=(FlowStep(name="synthesize", lobes=("synthesize",
                    "skill_select", "skill_active", "memory_recall", "session_recall"),
                    loop="single"),),
        )

    The named paths each have a default flow (qna / research /
    task_execute / clarify / relational / onboarding).
    Emergent paths have no default flow — the activated lobe set
    IS the answer (the interpreter handles this case).

    Per-bot customization is via the flat weight surface:
    - ``flow_disable_<flow>`` — flip a flow off
    - ``flow_<flow>__step_<step>__disable`` — skip a step
    - ``flow_<flow>__step_<step>__lobe_<lobe_id>__add`` / ``__remove``
      — mutate the step's lobe slice per-bot
    """

    name: str  # "qna" / "research" / "task_execute" / etc.
    steps: tuple[FlowStep, ...] = ()
    description: str = ""
    # When True, this flow can be auto-promoted from a trace-mined
    # emergent shape via ``mine_emergent_paths.py``. The flag is
    # informational — emergent flows can be promoted regardless.
    promotable: bool = True


def propagate_flow_steps(
    step_nodes: list[FlowStepNode],
    ctx: dict,
    *,
    weights: dict[str, float],
) -> list[dict]:
    """Resolve which flow-step nodes activate this turn (Phase 7+).

    Sibling of ``propagate_stage_nodes`` (Phase 7a) — same shape, but
    the namespacing is namespaced under ``flow_<flow>_<step>`` to make
    the two-axis separation explicit. The flow axis is **independent**
    of the lobe axis: a flow-side override (``flow_disable_*``) does
    not bleed into the lobe axis (``disable_*``).
    """
    out: list[dict] = []
    for node in sorted(step_nodes, key=lambda n: (n.flow, n.step, n.order, n.id)):
        disable_key = f"flow_disable_{node.flow}_{node.step}_{node.id}"
        prior_key = f"flow_prior_{node.flow}_{node.step}_{node.id}"
        min_key = f"flow_min_{node.flow}_{node.step}_{node.id}"
        disabled_by_overlay = bool(weights.get(disable_key, 0.0))
        prior = float(weights.get(prior_key, node.prior))
        min_act = float(weights.get(min_key, node.min_activation))
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
            fired = [k for k in signal_weight if float(raw_signals.get(k, 0.0)) > 0.0]
            reason = "+".join(fired) if fired else "prior_only"
        out.append(
            {
                "id": node.id,
                "flow": node.flow,
                "step": node.step,
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


def step_signal_node(step: FlowStep, flow: str) -> FlowStepNode | None:
    """RFC 0017 — a step's OWN first-class signal expressed as a ``FlowStepNode``,
    or ``None`` when the step declares no signal (the always-on structural
    default). Lets ``inspect_flow_axis`` / the trace surface a stage's signal
    alongside its ``state_nodes`` without a separate code path. Behavior-
    preserving: a step with empty ``signal_weights`` yields nothing.
    """
    if not step.signal_weights:
        return None
    return FlowStepNode(
        id=f"{step.name}:signal",
        flow=flow or "",
        step=step.name,
        signals=step.signals,
        signal_weights=dict(step.signal_weights),
        prior=step.prior,
        min_activation=step.min_activation,
        description=step.description,
    )


@dataclass
class FlowStepResult:
    """Phase 7+ — the result envelope for a single flow step's execution.

    A flow step's agentic loop runs ONE LLM call (or one tool loop)
    using the system prompt composed by the lobe axis. The result
    carries the text, the produced context nodes, the tool calls,
    and per-step trace metadata. Output flows forward to the next
    step in the flow; the final step's text IS the response.
    """

    flow: str
    step: str
    text: str = ""
    context_nodes: list[Any] = field(default_factory=list)
    tool_calls: list[Any] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def stage_name(self) -> str:
        """Deprecated StageResult compatibility alias."""
        return self.step

    @property
    def path(self) -> str:
        """Deprecated StageResult compatibility alias."""
        return self.flow


@dataclass
class BaseFlow:
    """Phase 7+ — the flow axis's executable contract.

    A ``BaseFlow`` subclass declares a named flow's runtime behavior:
    how its steps run, how the steps' outputs combine into the final
    response, and how the per-step trace is recorded. Default impl
    is a no-op — the interpreter's ``_run_pipeline`` provides the
    production runner; tests can subclass to drive a flow under a
    stub LLM / tool runtime.
    """

    flow: Flow

    def run_step(self, step: FlowStep, ctx: Any, *, weights: dict[str, float]) -> FlowStepResult:
        """Run a single step. Subclasses override with the production
        runner; the default is a no-op for tests / registry-only
        construction."""
        return FlowStepResult(flow=self.flow.name, step=step.name)

    def run(self, ctx: Any, *, weights: dict[str, float]) -> list[FlowStepResult]:
        """Run the entire flow's step sequence in order.

        Default impl: call ``run_step`` for each step in ``flow.steps``
        and collect the results. Subclasses can override the
        cross-step behavior (e.g., the production runner writes
        each step's context nodes to the Blackboard so the next
        step's lobe slice can see them).
        """
        return [self.run_step(step, ctx, weights=weights) for step in self.flow.steps]
