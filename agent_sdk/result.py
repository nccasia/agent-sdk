"""Ergonomic result + trace types — the public output surface.

``AgentResult`` is the ergonomic wrapper over the engine's ``FinalEnvelope``
(``contracts/memo.py``) plus the ``Trace``. ``Trace`` is the full, JSON-able
picture of a run; ``ActivationSnapshot`` is the dry, no-LLM routing probe;
``Optimization`` is a pure weight-patch proposal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agent_sdk.clients.messages import ProviderUsage
from agent_sdk.contracts.memo import Citation

__all__ = [
    "Usage",
    "Refusal",
    "MemoryUpdate",
    "AgentResult",
    "Trace",
    "ActivationSnapshot",
    "Optimization",
]

# Rough $/Mtok default (input, output) — overridable; only an estimate.
_DEFAULT_COST_PER_MTOK = (3.0, 15.0)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    estimated_cost: float = 0.0

    @classmethod
    def from_provider(
        cls, pu: ProviderUsage, *, cost_per_mtok: tuple[float, float] = _DEFAULT_COST_PER_MTOK
    ) -> Usage:
        cost = (pu.input_tokens / 1e6) * cost_per_mtok[0] + (
            pu.output_tokens / 1e6
        ) * cost_per_mtok[1]
        return cls(
            input_tokens=pu.input_tokens,
            output_tokens=pu.output_tokens,
            cache_read_tokens=pu.cache_read_tokens,
            cache_write_tokens=pu.cache_write_tokens,
            estimated_cost=round(cost, 6),
        )

    def to_json(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "estimated_cost": self.estimated_cost,
        }


@dataclass
class Refusal:
    reason: Literal["no_citations", "budget_exceeded", "policy_violation"]
    message: str = ""

    def to_json(self) -> dict:
        return {"reason": self.reason, "message": self.message}


@dataclass
class MemoryUpdate:
    action: str
    scope: str
    key: str

    def to_json(self) -> dict:
        return {"action": self.action, "scope": self.scope, "key": self.key}


@dataclass
class Trace:
    """The full, JSON-able picture of a run."""

    trace_id: str = ""
    path: dict = field(default_factory=dict)
    lobes: list[dict] = field(default_factory=list)
    flow_stages: list[dict] = field(default_factory=list)
    blackboard: dict = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    steps: list[dict] = field(default_factory=list)  # ReAct sub-steps per stage
    meta_actions: list[dict] = field(default_factory=list)
    # Per-hop model calls (stage/hop/stop_reason/usage/response/tool_results) — the
    # ReAct capture the visual viewer reads to reconstruct the reasoning timeline.
    llm_calls: list[dict] = field(default_factory=list)
    # Context telemetry (Phase 1): the attention/tier picture for the turn —
    # ``{nodes, tiers, tier_counts, stages}``. ``stages`` rolls up each stage's
    # input tokens + per-hop funnel-tail series. Empty when no node-emitting lobe
    # fires (default network) — strictly additive to the trace schema.
    attention: dict = field(default_factory=dict)
    # First-class adaptive-exposure telemetry, projected from the per-stage traces
    # (a clean host projection, no metadata digging). ``tool_selection``: one
    # ``{stage, kept, hinted, dropped}`` per stage that ran adaptive tool routing;
    # ``skill_selection``: one ``{stage, ranking:[…]}`` per stage that surfaced
    # on_demand skills. Empty for the static default (byte-identical).
    tool_selection: list[dict] = field(default_factory=list)
    skill_selection: list[dict] = field(default_factory=list)

    def timeline(self) -> list[dict]:
        """ReAct sub-steps across the run (thinking / tool_use / tool_result / answer)."""
        out: list[dict] = []
        for stage in self.flow_stages:
            out.append({"kind": "stage_start", "stage": stage.get("stage")})
            for sub in stage.get("steps", []):
                out.append(sub)
            out.append({"kind": "stage_end", "stage": stage.get("stage")})
        return out

    def to_json(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "path": self.path,
            "lobes": self.lobes,
            "flow_stages": self.flow_stages,
            "blackboard": self.blackboard,
            "usage": self.usage.to_json(),
            "meta_actions": self.meta_actions,
            "llm_calls": self.llm_calls,
            "attention": self.attention,
            "tool_selection": self.tool_selection,
            "skill_selection": self.skill_selection,
        }


@dataclass
class AgentResult:
    text: str = ""
    status: Literal["answered", "refused"] = "answered"
    citations: list[Citation] = field(default_factory=list)
    refusal: Refusal | None = None
    usage: Usage = field(default_factory=Usage)
    memory_updates: list[MemoryUpdate] = field(default_factory=list)
    trace: Trace = field(default_factory=Trace)

    def __str__(self) -> str:
        return self.text

    def to_json(self) -> dict:
        return {
            "text": self.text,
            "status": self.status,
            "citations": [c.model_dump() for c in self.citations],
            "refusal": self.refusal.to_json() if self.refusal else None,
            "usage": self.usage.to_json(),
            "memory_updates": [m.to_json() for m in self.memory_updates],
            "trace_id": self.trace.trace_id,
        }


@dataclass
class ActivationSnapshot:
    """The dry, no-LLM routing probe (``agent.inspect``)."""

    path: tuple[str, float] = ("emergent", 0.0)
    lobes: list[dict] = field(default_factory=list)
    flow: list[str] = field(default_factory=list)
    budget: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "path": list(self.path),
            "lobes": self.lobes,
            "flow": self.flow,
            "budget": self.budget,
        }


@dataclass
class Optimization:
    axis: str
    target: str
    reason: str
    weight_patch: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "axis": self.axis,
            "target": self.target,
            "reason": self.reason,
            "weight_patch": self.weight_patch,
        }
