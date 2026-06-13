"""FlowRegistry — the per-turn view of the flow axis.

Mirrors ``LobeRegistry`` but is **independent** of it. A new flow is
a registry row (or a default entry in ``default_flows()``); a new
lobe is a separate registry row. The two registries are
**independently testable, optimizable, and extensible** — a new flow
doesn't require touching any lobe; a new lobe doesn't require
touching any flow.

The flow registry carries the **flow axis** (the progressive-
execution second axis): ``default_flows()`` provides the 7 named
paths' flows; ``get_flow(name)`` is the lookup seam;
``compose_step_prompt(step, ctx, weights)`` is the bridge to the
lobe axis (each step's system prompt is composed from the lobes in
its slice, via the lobe's LobeNode state machine under the live
TurnContext).

The flow registry **does not** know about lobes directly — it
queries the ``LobeRegistry`` for the lobes in each step's slice.
This is the explicit one-way dependency: flows depend on lobes;
lobes do not depend on flows.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_sdk.flows.flow import Flow, FlowStep

__all__ = ["FlowRegistry", "Flow", "set_default_flows"]

# Default-flow provider — the PROJECT (``agent_core.flows``) registers its
# ``default_flows`` here at import time so the SDK registry carries no concrete
# flow definitions (framework, not instances). Defaults to empty for standalone
# SDK use.
_default_flows: Callable[[], list[Flow]] = lambda: []


def set_default_flows(provider: Callable[[], list[Flow]]) -> None:
    """Register the project's default-flow provider (called at import time)."""
    global _default_flows
    _default_flows = provider


class FlowRegistry:
    """Per-turn view of the flow axis — the second-axis mirror of
    ``LobeRegistry``.

    Defaults are the 7 named paths' flows (``default_flows()``);
    rows (dicts) override or extend by name. ``from_rows`` /
    ``add_row`` are the G6 seam: a new flow is a registry row
    with steps + per-step lobe slices — never an interpreter branch.
    """

    def __init__(self, flows: list[Flow] | None = None):
        flow_list = flows if flows is not None else _default_flows()
        self._flows: dict[str, Flow] = {f.name: f for f in flow_list}

    @classmethod
    def from_rows(cls, flow_rows: list[dict] | None = None) -> FlowRegistry:
        registry = cls()
        for row in flow_rows or []:
            registry.add_row(row)
        return registry

    def flows(self) -> list[Flow]:
        return list(self._flows.values())

    def get(self, name: str) -> Flow | None:
        return self._flows.get(name)

    def register(self, flow: Flow) -> None:
        self._flows[flow.name] = flow

    def remove(self, name: str) -> None:
        self._flows.pop(name, None)

    def steps_for_path(self, path_name: str) -> tuple[FlowStep, ...]:
        """The default step sequence for a path (Phase 7+ flow axis).

        Returns the steps in declaration order. Emergent paths have
        no default flow — the caller is expected to handle that
        case (``()`` returns here).
        """
        flow = self._flows.get(path_name)
        if flow is None:
            return ()
        return flow.steps

    def get_step(self, flow: str, name: str) -> FlowStep | None:
        """Lookup a step by (flow, name) pair."""
        f = self._flows.get(flow)
        if f is None:
            return None
        for step in f.steps:
            if step.name == name:
                return step
        return None

    def add_row(self, row: dict) -> Flow:
        """Register a flow from a declarative registry row (no code).

        The G6 seam — adding a new pipeline is one row in the
        registry. The interpreter picks it up at the next turn
        (per-turn hot-reload; no cache).
        """
        from agent_sdk.flows.flow import FlowStep

        steps = []
        for step_row in row.get("steps", ()):
            steps.append(
                FlowStep(
                    name=str(step_row["name"]),
                    lobes=tuple(str(lobe_id) for lobe_id in (step_row.get("lobes") or ())),
                    loop=str(step_row.get("loop", "single")),
                    tools=tuple(str(tool_id) for tool_id in (step_row.get("tools") or ())),
                    description=str(step_row.get("description", "")),
                )
            )
        flow = Flow(
            name=str(row["name"]),
            steps=tuple(steps),
            description=str(row.get("description", "")),
        )
        self.register(flow)
        return flow

    def customize_for_bot(
        self, flow_name: str, *, weights: dict[str, float]
    ) -> tuple[FlowStep, ...] | None:
        """Phase 7e — per-bot flow customization.

        Reads the flat weight surface and mutates the named flow's
        step sequence per-bot:

        - ``flow_<flow>__step_<step>__disable = 1.0`` — skip the step
        - ``flow_<flow>__step_<step>__lobe_<lobe_id>__add = 1.0`` —
          add a lobe to the step's slice
        - ``flow_<flow>__step_<step>__lobe_<lobe_id>__remove = 1.0`` —
          remove a lobe from the step's slice
        - ``flow_disable_<flow> = 1.0`` — flip the entire flow off

        Returns the customized step sequence, or ``None`` if the
        flow was disabled entirely. Emergent paths (no flow in the
        registry) return ``()`` — the activated lobe set IS the
        answer (the interpreter handles this case).

        The customization is **additive** — the flow registry's
        default flow is unchanged; this returns a new step sequence
        (per-turn, not cached). The interpreter calls this on
        every turn so a DB change shows up immediately.
        """
        flow = self._flows.get(flow_name)
        if flow is None:
            return ()
        # 1. Whole-flow disable.
        if bool(weights.get(f"flow_disable_{flow_name}", 0.0)):
            return None
        # 2. Per-step disable + lobe-slice mutations.
        customized: list[FlowStep] = []
        for step in flow.steps:
            disable_key = f"flow_{flow_name}__step_{step.name}__disable"
            if bool(weights.get(disable_key, 0.0)):
                continue
            new_lobes: list[str] = list(step.lobes)
            mutated = False
            for lobe_id in list(step.lobes):
                rm_key = f"flow_{flow_name}__step_{step.name}__lobe_{lobe_id}__remove"
                if bool(weights.get(rm_key, 0.0)) and lobe_id in new_lobes:
                    new_lobes.remove(lobe_id)
                    mutated = True
            for lobe_id, val in weights.items():
                prefix = f"flow_{flow_name}__step_{step.name}__lobe_"
                suffix = "__add"
                if lobe_id.startswith(prefix) and lobe_id.endswith(suffix):
                    lobe_name = lobe_id[len(prefix) : -len(suffix)]
                    if bool(val) and lobe_name not in new_lobes:
                        new_lobes.append(lobe_name)
                        mutated = True
            if mutated:
                customized.append(
                    FlowStep(
                        name=step.name,
                        lobes=tuple(new_lobes),
                        loop=step.loop,
                        tools=step.tools,
                        description=step.description,
                        fanout_key=step.fanout_key,
                        state_nodes=step.state_nodes,
                    )
                )
            else:
                customized.append(step)
        return tuple(customized)

    def compose_step_prompt(
        self,
        step: FlowStep,
        ctx: Any,
        lobe_registry: Any,
        *,
        weights: dict | None = None,
    ) -> list:
        """Phase 7+ — bridge the flow axis to the lobe axis.

        The flow step references a slice of lobes. This method
        consults the ``lobe_registry`` (LobeRegistry) for each lobe
        in the slice and calls the lobe's ``build_context(ctx)`` to
        collect the live ``ContextNode``s the lobe emits under the
        live TurnContext.

        The two-axis separation: the flow axis is **independent** of
        the lobe axis's internals. The flow only knows the lobe
        *interface* (``build_context(ctx)``) — not the LobeNode
        state machine, not the signals, not the per-bot priors.
        Each lobe is a passive provider; the flow is the active
        orchestrator that consults them.

        The flow registry **depends on** the lobe registry (to know
        what lobes exist + how to call them). The lobe registry does
        **not** depend on the flow registry. This is the explicit
        one-way dependency between the two axes.
        """
        if not step.lobes:
            return []
        if lobe_registry is None:
            return []
        out: list = []
        for lobe_id in step.lobes:
            lobe = lobe_registry.get_lobe(lobe_id) if hasattr(lobe_registry, "get_lobe") else None
            if lobe is None:
                continue
            try:
                nodes = list(lobe.build_context(ctx) or [])
            except Exception:
                nodes = []
            out.extend(nodes)
        return out
