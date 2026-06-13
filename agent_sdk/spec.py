"""``PreactSpec`` — the whole PreAct configuration as data (portability).

The deterministic core (intent recognition, activation, attention/budget, flow
resolution) is a pure function of ``(spec, context)``. ``build_spec`` captures an
agent's network as JSON; ``agent_from_spec`` rebuilds it (wiring only the I/O
seams — client + tools — afresh). Declarative flow signals round-trip exactly;
Python-callable lobe activations are captured structurally (an ``always_on`` flag
+ prior/threshold) — full signal fidelity needs declarative signals (porting.md).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from agent_sdk.contracts.pins import PINNED_LOBES
from agent_sdk.flow_def import flow as make_flow
from agent_sdk.lobes.runtime import Lobe
from agent_sdk.skill_def import Skill
from agent_sdk.stages import stage as make_stage

__all__ = ["PreactSpec", "build_spec", "agent_from_spec"]

SPEC_VERSION = "1"


@dataclass
class PreactSpec:
    version: str = SPEC_VERSION
    instructions: str = ""
    lobes: list[dict] = field(default_factory=list)
    stages: list[dict] = field(default_factory=list)
    flows: list[dict] = field(default_factory=list)
    skills: list[dict] = field(default_factory=list)
    weights: dict = field(default_factory=dict)
    budgets: dict = field(default_factory=dict)
    # Named authoring aliases (1:1 with a Mezon BotPolicy). ``weights``/``budgets``
    # stay the canonical surface the engine consumes and what ``build_spec``
    # captures; a host hand-authoring a spec from a BotPolicy may instead set these
    # named fields and ``agent_from_spec`` folds them into weights/budgets. Empty by
    # default ⇒ no effect (the generic surface already round-trips).
    flow_lobe_weights: dict = field(default_factory=dict)
    flow_layer_budgets: dict = field(default_factory=dict)
    pinned_lobes: list[str] = field(default_factory=lambda: sorted(PINNED_LOBES))
    require_citations: bool = False
    tz: str = "UTC"
    lang: str = "en"

    def to_json(self) -> dict:
        return asdict(self)

    def to_json_str(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_json(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, data: dict | str) -> PreactSpec:
        if isinstance(data, str):
            data = json.loads(data)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _lobe_row(lobe: Lobe) -> dict:
    try:
        always_on = lobe.activation({}) > 0
    except Exception:
        always_on = False
    # Façade ``Lobe``s carry these as attributes; ported ``BaseLobe``s expose
    # them via ``.spec`` (a ``LobeSpec``). Read whichever is present.
    spec = getattr(lobe, "spec", None)

    def _g(attr: str, default: Any) -> Any:
        if hasattr(lobe, attr) and not isinstance(getattr(type(lobe), attr, None), property):
            return getattr(lobe, attr, default)
        return getattr(spec, attr, default) if spec is not None else default

    return {
        "id": lobe.id,
        "name": getattr(lobe, "name", "") or lobe.id,
        "description": getattr(lobe, "description", ""),
        "use_when": getattr(lobe, "use_when", ""),
        "layer": int(_g("layer", 4)),
        "behavior": _g("behavior", "recall"),
        "pinned": bool(_g("pinned", False)),
        "prior": float(_g("prior", 0.0)),
        "threshold": float(getattr(lobe, "threshold", None) or _g("min_activation", 0.5)),
        "order": int(_g("order", 0)),
        "writes": list(_g("writes", ())),
        "excites": dict(getattr(lobe, "excites", None) or getattr(spec, "edges", {}) or {}),
        "system_prompt": getattr(lobe, "system_prompt", None),
        "always_on": always_on,
    }


def _stage_row(s: Any) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "use_when": s.use_when,
        "lobes": list(s.lobes),
        "loop": s.loop,
        "tools": list(s.tools),
        "fanout_key": s.fanout_key,
        "threshold": float(s.threshold),
        "model": s.model,
        "temperature": s.temperature,
        "max_tokens": s.max_tokens,
        "hops": s.hops,
        "system_prompt": s.system_prompt,
    }


def _flow_row(f: Any) -> dict:
    return {
        "id": f.id,
        "name": f.name,
        "description": f.description,
        "use_when": f.use_when,
        "stages": list(f.stages),
        "threshold": float(f.threshold),
        "grounds": bool(f.grounds),
        "signal": getattr(f, "signal_expr", None),
    }


def _skill_row(pack: Any) -> dict:
    return {
        "id": pack.id,
        "name": pack.name,
        "description": pack.description,
        "instructions": pack.instructions,
        "stages": list(pack.stages),
        "tools": list(pack.required_tools),
        "disclosure": pack.injection,
        "files": dict(pack.files),
        "checklist": [dict(c) for c in getattr(pack, "checklist", ())],
        "context_vars": [dict(v) for v in getattr(pack, "context_vars", ())],
    }


def build_spec(agent: Any) -> PreactSpec:
    eng = agent.engine
    return PreactSpec(
        instructions=eng.instructions,
        lobes=[_lobe_row(lb) for lb in eng.lobes],
        stages=[_stage_row(s) for s in eng.stage_registry.stages()],
        flows=[_flow_row(f) for f in eng.flows],
        skills=[_skill_row(p) for p in eng.skill_packs],
        weights=dict(eng.weights),
        budgets=dict(eng.budgets),
        require_citations=eng.require_citations,
        tz=eng.tz,
        lang=eng.lang,
    )


class _SpecLobe(Lobe):
    """A lobe rebuilt from a spec row (structure + system prompt preserved)."""

    def __init__(self, row: dict):
        self.id = row["id"]
        self.name = row.get("name", row["id"])
        self.description = row.get("description", "")
        self.use_when = row.get("use_when", "")
        self.layer = int(row.get("layer", 4))
        self.behavior = row.get("behavior", "recall")
        self.pinned = bool(row.get("pinned", False))
        self.prior = float(row.get("prior", 0.0))
        self.threshold = float(row.get("threshold", 0.5))
        self.order = int(row.get("order", 0))
        self.writes = tuple(row.get("writes", ()))
        self.excites = dict(row.get("excites", {}))
        self.system_prompt = row.get("system_prompt")
        self._always_on = bool(row.get("always_on", False))

    def activation(self, ctx: dict) -> float:
        return 1.0 if self._always_on else 0.0


def agent_from_spec(
    spec: PreactSpec | dict | str, *, client: Any, tools: list[Any] | None = None, **overrides: Any
) -> Any:
    from agent_sdk.agent import PreactAgent

    if not isinstance(spec, PreactSpec):
        spec = PreactSpec.from_json(spec)

    lobes = [_SpecLobe(r) for r in spec.lobes]
    stages = [
        make_stage(
            r["id"],
            name=r.get("name"),
            description=r.get("description", ""),
            use_when=r.get("use_when", ""),
            lobes=r.get("lobes", []),
            loop=r.get("loop", "single"),
            tools=r.get("tools", []),
            fanout_key=r.get("fanout_key", ""),
            threshold=r.get("threshold", 0.0),
            model=r.get("model"),
            temperature=r.get("temperature"),
            max_tokens=r.get("max_tokens"),
            hops=r.get("hops"),
            system_prompt=r.get("system_prompt"),
        )
        for r in spec.stages
    ]
    flows = [
        make_flow(
            r["id"],
            name=r.get("name"),
            description=r.get("description", ""),
            use_when=r.get("use_when", ""),
            stages=r.get("stages", []),
            threshold=r.get("threshold", 0.5),
            grounds=r.get("grounds", True),
            signal=r.get("signal"),
        )
        for r in spec.flows
    ]
    skills = [
        Skill(
            r["id"],
            when=r.get("use_when", r.get("description", "")),
            instructions=r.get("instructions", ""),
            tools=r.get("tools", []),
            disclosure=r.get("disclosure", "on_demand"),
            files=r.get("files", {}),
            name=r.get("name", ""),
            description=r.get("description", ""),
            stages=r.get("stages", []),
            checklist=r.get("checklist", []),
            context_vars=r.get("context_vars", []),
        )
        for r in spec.skills
    ]
    # Fold the named authoring aliases into the canonical surfaces (the named
    # fields win a key collision — they are the explicit BotPolicy intent).
    weights = {**spec.weights, **(spec.flow_lobe_weights or {})}
    budgets = {**spec.budgets, **(spec.flow_layer_budgets or {})}
    kwargs = dict(
        client=client,
        instructions=spec.instructions,
        lobes=lobes,
        stages=stages,
        flows=flows,
        skills=skills or None,
        tools=tools,
        weights=weights,
        budgets=budgets,
        require_citations=spec.require_citations,
        tz=spec.tz,
        lang=spec.lang,
    )
    kwargs.update(overrides)
    return PreactAgent(**kwargs)
