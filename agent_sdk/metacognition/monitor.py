"""Metacognitive monitoring over OX/OY snapshots."""

from __future__ import annotations

from agent_sdk.inspection import EngineSnapshot, FlowAxisSnapshot, LobeAxisSnapshot
from agent_sdk.metacognition.model import MetaObservation


def monitor(
    *,
    lobe_axis: LobeAxisSnapshot | None = None,
    flow_axis: FlowAxisSnapshot | None = None,
    engine: EngineSnapshot | None = None,
) -> tuple[MetaObservation, ...]:
    """Observe object-level thinking state without mutating it."""
    observations: list[MetaObservation] = []

    if flow_axis is not None:
        for step in flow_axis.steps:
            if step.disabled:
                observations.append(
                    MetaObservation(
                        id=f"flow:{step.flow}.{step.step}:disabled",
                        kind="step_disabled",
                        target=f"{step.flow}.{step.step}",
                        severity=0.3,
                        detail="flow customization disabled this step",
                    )
                )
            if not step.lobes:
                observations.append(
                    MetaObservation(
                        id=f"flow:{step.flow}.{step.step}:empty_lobe_slice",
                        kind="empty_lobe_slice",
                        target=f"{step.flow}.{step.step}",
                        severity=0.8,
                        detail="step has no lobes to consult",
                    )
                )
            for node in step.state_nodes:
                if node.get("id") == "context:tight" and node.get("activated"):
                    observations.append(
                        MetaObservation(
                            id=f"flow:{step.flow}.{step.step}:context_tight",
                            kind="context_tight",
                            target=f"{step.flow}.{step.step}",
                            severity=0.75,
                            detail="context window pressure detected",
                        )
                    )
                if node.get("id") == "context:open" and node.get("activated"):
                    observations.append(
                        MetaObservation(
                            id=f"flow:{step.flow}.{step.step}:context_open",
                            kind="context_open",
                            target=f"{step.flow}.{step.step}",
                            severity=0.2,
                            detail="context window has room",
                        )
                    )

    if lobe_axis is not None:
        for lobe in lobe_axis.lobes:
            if lobe.state_nodes and not any(node.get("activated") for node in lobe.state_nodes):
                observations.append(
                    MetaObservation(
                        id=f"lobe:{lobe.id}:inactive_group",
                        kind="inactive_lobe_group",
                        target=lobe.id,
                        severity=0.4,
                        detail="all state nodes for this lobe stayed inactive",
                    )
                )

    if engine is not None:
        path = engine.path or {}
        if path.get("emergent") or (
            path.get("score") is not None and float(path.get("score") or 0) < 0.55
        ):
            observations.append(
                MetaObservation(
                    id="engine:path:low_confidence",
                    kind="low_confidence_path",
                    target=str(path.get("name") or "unknown"),
                    severity=0.7,
                    detail="path recognition is emergent or low-confidence",
                )
            )
        for step in engine.flow_steps:
            flow = str(step.get("flow") or "")
            name = str(step.get("step") or "")
            if flow and name and int(step.get("node_count") or 0) == 0:
                observations.append(
                    MetaObservation(
                        id=f"engine:{flow}.{name}:empty_step_context",
                        kind="empty_step_context",
                        target=f"{flow}.{name}",
                        severity=0.65,
                        detail="executed step produced no lobe context nodes",
                    )
                )

    return tuple(observations)
