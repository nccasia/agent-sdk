"""The built-in production network — the agent-core lobes / paths / flows / stages.

This maps the faithfully-ported production network (``agent_sdk/lobes/network.py``
+ ``agent_sdk/flows/defaults.py`` — 18 lobes, 8 paths, the named flows) onto the
engine's drive surface:

- **lobes** → the ported ``LOBE`` instances (they contribute context via
  ``build_context`` through the Phase-0 pipeline).
- **paths** → the ported ``PathSpec`` recognizers (free, deterministic intent
  recognition + member-lobe bias) — fed straight to ``propagate``.
- **stages** → each flow's ``FlowStep``s as façade :class:`Stage`s, **flow-
  qualified** (``"<flow>:<step>"``) so per-flow lobe slices are preserved.
- **flows** → façade :class:`Flow`s whose recognizer signal is the matching
  path's recognizer (so ``resolve_path`` picks the flow) and whose stage list
  references the flow-qualified stage ids.

This is the new builtin default for :class:`PreactAgent`.
"""

from __future__ import annotations

from agent_sdk.flow_def import Flow
from agent_sdk.flow_def import flow as _flow
from agent_sdk.flows.defaults import default_flows
from agent_sdk.lobes.network import default_lobe_objects, default_paths
from agent_sdk.lobes.runtime import Lobe
from agent_sdk.stages import Stage
from agent_sdk.stages import stage as _stage

__all__ = [
    "production_lobes",
    "production_paths",
    "production_stages",
    "production_flows",
]


def production_lobes() -> list[Lobe]:
    """The 18 ported production lobes (memory/skill/tool/task/cognition/expression)."""
    return list(default_lobe_objects())


def production_paths() -> list:
    """The 8 ported ``PathSpec`` recognizers (fed to ``propagate``)."""
    return list(default_paths())


def _qualified(flow_name: str, step_name: str) -> str:
    return f"{flow_name}:{step_name}"


def production_stages() -> list[Stage]:
    """Each flow's ``FlowStep``s as flow-qualified façade Stages."""
    out: list[Stage] = []
    seen: set[str] = set()
    for fl in default_flows():
        for step in fl.steps:
            sid = _qualified(fl.name, step.name)
            if sid in seen:
                continue
            seen.add(sid)
            out.append(
                _stage(
                    sid,
                    name=step.name,
                    lobes=list(step.lobes),
                    loop=step.loop,
                    tools=list(step.tools),
                    fanout_key=step.fanout_key,
                    description=step.description,
                    model=step.model,
                    temperature=step.temperature,
                    max_tokens=step.max_tokens,
                    hops=step.hops,
                    system_prompt=step.system_prompt,
                )
            )
    return out


def production_flows() -> list[Flow]:
    """Façade Flows wired to the ported path recognizers + flow-qualified stages."""
    paths = {p.name: p for p in default_paths()}
    out: list[Flow] = []
    for fl in default_flows():
        p = paths.get(fl.name)
        stage_ids = [_qualified(fl.name, step.name) for step in fl.steps]
        out.append(
            _flow(
                fl.name,
                name=fl.name,
                description=fl.description,
                stages=stage_ids,
                grounds=bool(getattr(p, "grounds", False)) if p else False,
                threshold=float(getattr(p, "threshold", 0.5)) if p else 0.5,
                # The path recognizer IS the flow's recognition signal; flows
                # without a named path (e.g. ``fallback``) stay dark and serve as
                # the emergent fallback.
                signal=(p.recognizer if p else (lambda _ctx: 0.0)),
            )
        )
    return out
