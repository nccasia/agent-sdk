"""Read-only inspection and optimization helpers for lobe/flow axes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from agent_sdk.flows.flow import propagate_flow_steps, step_signal_node
from agent_sdk.flows.registry import FlowRegistry
from agent_sdk.lobes.registry import LobeRegistry


@dataclass(frozen=True)
class LobeInspection:
    id: str
    layer: int
    activated: bool
    state_nodes: list[dict] = field(default_factory=list)
    context_node_count: int = 0
    write_meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LobeAxisSnapshot:
    lobes: list[LobeInspection]
    activated: list[str]

    def to_payload(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FlowStepInspection:
    flow: str
    step: str
    loop: str
    tools: list[str]
    lobes: list[str]
    type: str = "simple"  # RFC 0017: the stage's running model (react/simple/map/none)
    disabled: bool = False
    state_nodes: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class FlowAxisSnapshot:
    flow: str
    disabled: bool
    steps: list[FlowStepInspection]

    def to_payload(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EngineSnapshot:
    path: dict | None = None
    flow: dict | None = None
    lobes: list[dict] = field(default_factory=list)
    flow_steps: list[dict] = field(default_factory=list)
    blackboard: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AxisOptimization:
    axis: str
    target: str
    reason: str
    weight_patch: dict[str, float] = field(default_factory=dict)

    def to_payload(self) -> dict:
        return asdict(self)


def build_meta_input(
    *,
    lobe_axis: LobeAxisSnapshot | None = None,
    flow_axis: FlowAxisSnapshot | None = None,
    engine: EngineSnapshot | None = None,
) -> dict[str, Any]:
    """Package object-level OX/OY snapshots for the metacognition layer."""
    return {
        "lobe_axis": lobe_axis,
        "flow_axis": flow_axis,
        "engine": engine,
    }


def inspect_lobe_axis(
    registry: LobeRegistry,
    ctx: Any,
    weights: dict[str, float] | None = None,
) -> LobeAxisSnapshot:
    """Snapshot lobe activation and per-node state without mutating runtime state."""
    weights = weights if isinstance(weights, dict) else {}
    active = set(getattr(ctx, "active_lobes", frozenset()) or ())
    rows: list[LobeInspection] = []
    board = getattr(ctx, "blackboard", None)
    for spec in registry.lobes():
        lobe = registry.get_lobe(spec.id)
        state_nodes: list[dict] = []
        context_count = 0
        if lobe is not None:
            try:
                state_nodes = list(lobe.activated_nodes(ctx, weights=weights) or [])
            except Exception:
                state_nodes = []
            try:
                context_count = len(list(lobe.build_context(ctx) or []))
            except Exception:
                context_count = 0
        write_meta = {}
        if board is not None and hasattr(board, "get_write_meta"):
            try:
                write_meta = dict(board.get_write_meta(spec.id) or {})
            except Exception:
                write_meta = {}
        activated = spec.id in active or any(node.get("activated") for node in state_nodes)
        rows.append(
            LobeInspection(
                id=spec.id,
                layer=spec.layer,
                activated=activated,
                state_nodes=state_nodes,
                context_node_count=context_count,
                write_meta=write_meta,
            )
        )
    return LobeAxisSnapshot(lobes=rows, activated=[row.id for row in rows if row.activated])


def inspect_flow_axis(
    registry: FlowRegistry,
    path: str,
    weights: dict[str, float] | None = None,
    ctx: dict | None = None,
) -> FlowAxisSnapshot:
    """Snapshot selected flow sequence after per-bot customization."""
    weights = weights if isinstance(weights, dict) else {}
    ctx = ctx if isinstance(ctx, dict) else {}
    steps = registry.customize_for_bot(path, weights=weights)
    if steps is None:
        return FlowAxisSnapshot(flow=path, disabled=True, steps=[])
    flow = registry.get(path)
    default_steps = flow.steps if flow is not None else ()
    default_names = {step.name for step in default_steps}
    active_names = {step.name for step in steps}
    out: list[FlowStepInspection] = []
    for step in steps:
        # RFC 0017: a step's OWN first-class signal is surfaced alongside its
        # state_nodes (prepended). Behavior-preserving — a step with no declared
        # signal contributes nothing.
        sig = step_signal_node(step, path)
        nodes = [sig, *step.state_nodes] if sig is not None else list(step.state_nodes)
        state_nodes = propagate_flow_steps(nodes, ctx, weights=weights) if nodes else []
        out.append(
            FlowStepInspection(
                flow=path,
                step=step.name,
                type=getattr(step, "type", "simple"),
                loop=step.loop,
                tools=list(step.tools),
                lobes=list(step.lobes),
                disabled=False,
                state_nodes=state_nodes,
            )
        )
    for name in sorted(default_names - active_names):
        step = registry.get_step(path, name)
        if step is None:
            continue
        out.append(
            FlowStepInspection(
                flow=path,
                step=step.name,
                type=getattr(step, "type", "simple"),
                loop=step.loop,
                tools=list(step.tools),
                lobes=list(step.lobes),
                disabled=True,
            )
        )
    return FlowAxisSnapshot(flow=path, disabled=False, steps=out)


def snapshot_engine(trace: Any, blackboard: Any | None = None) -> EngineSnapshot:
    """Combine trace and optional blackboard state into one serializable snapshot."""
    nodes = []
    layer_budgets = {}
    if blackboard is not None:
        try:
            nodes = list(getattr(blackboard, "nodes", []) or [])
        except Exception:
            nodes = []
        if hasattr(blackboard, "layer_budgets"):
            try:
                layer_budgets = dict(blackboard.layer_budgets() or {})
            except Exception:
                layer_budgets = {}
    answer = getattr(trace, "answer", None) or getattr(trace, "response", None)
    return EngineSnapshot(
        path=getattr(trace, "path", None),
        flow=getattr(trace, "flow", None),
        lobes=list(getattr(trace, "lobes", None) or []),
        flow_steps=list(getattr(trace, "flow_steps", None) or []),
        blackboard={"node_count": len(nodes), "layer_budgets": layer_budgets},
        response={"text_len": len(str(answer or ""))},
    )


def suggest_axis_optimizations(snapshot: EngineSnapshot) -> list[AxisOptimization]:
    """Return pure optimization proposals; callers decide whether to apply them."""
    suggestions: list[AxisOptimization] = []
    for step in snapshot.flow_steps:
        flow = str(step.get("flow") or "")
        name = str(step.get("step") or "")
        node_count = int(step.get("node_count") or 0)
        if flow and name and node_count == 0:
            suggestions.append(
                AxisOptimization(
                    axis="flow",
                    target=f"{flow}.{name}",
                    reason="step produced no lobe context nodes in this snapshot",
                    weight_patch={f"flow_{flow}__step_{name}__disable": 1.0},
                )
            )
    for lobe in snapshot.lobes:
        lobe_id = str(lobe.get("id") or "")
        summary = lobe.get("state_node_summary") or {}
        total = int(summary.get("total") or 0)
        enabled = int(summary.get("enabled") or 0)
        if lobe_id and total and enabled == 0:
            suggestions.append(
                AxisOptimization(
                    axis="lobe",
                    target=lobe_id,
                    reason="all lobe state nodes stayed inactive in this snapshot",
                    weight_patch={f"prior_{lobe_id}": -0.1},
                )
            )
    return suggestions
