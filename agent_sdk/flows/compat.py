"""Deprecated stage-axis compatibility adapters.

The active progressive-execution model lives in ``agent_sdk.flows`` as
``Flow`` / ``FlowStep``. These names keep the Phase 7a-d ``Stage`` API
importable for one migration cycle while deriving defaults from the flow
registry instead of maintaining a second model.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

from agent_sdk.flows.defaults import default_flows
from agent_sdk.flows.flow import FlowStep, FlowStepNode, FlowStepResult, propagate_flow_steps

__all__ = [
    "Stage",
    "StageNode",
    "StageResult",
    "default_stages",
    "propagate_stage_nodes",
]


@dataclass(frozen=True)
class StageNode(FlowStepNode):
    """Deprecated alias for ``FlowStepNode``.

    ``stage`` maps to ``step`` and uses a synthetic ``legacy`` flow when the
    old API does not provide a path/flow name.
    """

    def __init__(self, id: str, stage: str, **kwargs: Any):
        warn = bool(kwargs.pop("_warn", True))
        if warn:
            warnings.warn(
                "StageNode is deprecated; use agent_sdk.flows.FlowStepNode",
                DeprecationWarning,
                stacklevel=2,
            )
        super().__init__(id=id, flow=str(kwargs.pop("flow", "legacy")), step=stage, **kwargs)

    @property
    def stage(self) -> str:
        return self.step


@dataclass(frozen=True)
class Stage(FlowStep):
    """Deprecated alias for ``FlowStep`` with ``path`` mapped to ``flow``."""

    path: str = ""

    def __init__(self, name: str, path: str, **kwargs: Any):
        warn = bool(kwargs.pop("_warn", True))
        if warn:
            warnings.warn(
                "Stage is deprecated; use agent_sdk.flows.FlowStep",
                DeprecationWarning,
                stacklevel=2,
            )
        object.__setattr__(self, "path", path)
        FlowStep.__init__(self, name=name, **kwargs)


@dataclass
class StageResult(FlowStepResult):
    """Deprecated alias for ``FlowStepResult``."""

    def __init__(self, stage_name: str, path: str, **kwargs: Any):
        warn = bool(kwargs.pop("_warn", True))
        if warn:
            warnings.warn(
                "StageResult is deprecated; use agent_sdk.flows.FlowStepResult",
                DeprecationWarning,
                stacklevel=2,
            )
        super().__init__(flow=path, step=stage_name, **kwargs)


def default_stages() -> list[Stage]:
    """Deprecated adapter over ``default_flows()``."""
    stages: list[Stage] = []
    for flow in default_flows():
        for step in flow.steps:
            stages.append(
                Stage(
                    name=step.name,
                    path=flow.name,
                    lobes=step.lobes,
                    loop=step.loop,
                    tools=step.tools,
                    description=step.description,
                    fanout_key=step.fanout_key,
                    state_nodes=tuple(
                        StageNode(
                            id=node.id,
                            stage=node.step,
                            flow=node.flow,
                            prior=node.prior,
                            signals=node.signals,
                            signal_weights=node.signal_weights,
                            min_activation=node.min_activation,
                            order=node.order,
                            description=node.description,
                            enabled_default=node.enabled_default,
                            produce=node.produce,
                            prompt=node.prompt,
                            _warn=False,
                        )
                        for node in step.state_nodes
                    ),
                    _warn=False,
                )
            )
    return stages


def propagate_stage_nodes(
    nodes: list[StageNode],
    ctx: dict,
    *,
    weights: dict[str, float],
) -> list[dict]:
    """Deprecated adapter over ``propagate_flow_steps``.

    Accepts old ``stage_*`` overlay keys by translating them into the flow
    namespace before propagation, then maps ``step`` back to ``stage`` in
    the returned trace dictionaries.
    """
    warnings.warn(
        "propagate_stage_nodes is deprecated; use agent_sdk.flows.propagate_flow_steps",
        DeprecationWarning,
        stacklevel=2,
    )
    translated = dict(weights or {})
    for key, value in list(translated.items()):
        if key.startswith("stage_disable_"):
            rest = key[len("stage_disable_") :]
            translated[f"flow_disable_legacy_{rest}"] = value
        elif key.startswith("stage_prior_"):
            rest = key[len("stage_prior_") :]
            translated[f"flow_prior_legacy_{rest}"] = value
        elif key.startswith("stage_min_"):
            rest = key[len("stage_min_") :]
            translated[f"flow_min_legacy_{rest}"] = value
    out = propagate_flow_steps(list(nodes), ctx, weights=translated)
    for entry in out:
        entry["stage"] = entry.get("step")
    return out
