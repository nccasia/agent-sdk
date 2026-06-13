"""Probe — run one real turn and capture the engine's internal behavior.

A good benchmark *sees* what actually fired, not just the final answer. ``probe``
drives a ``PreactAgent`` through one turn and collects a structured, JSON-able
record of its internals — recognized flow + score, per-lobe activation
(id/layer/score/reason), the flow stages with their ReAct sub-steps
(thinking / tool_use → tool_result / answer), tool calls, usage, and the final
result. Feed the records to :func:`agent_sdk.report.render_html` for a
self-contained visual report.

It is tier-agnostic: the client lives on the agent the caller builds, so the same
code probes a ``FakeClient`` turn (deterministic) or a real-provider turn.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from agent_sdk.events import Final, ToolCall, ToolResult

__all__ = ["ProbeRecord", "probe"]


@dataclass
class ProbeRecord:
    """The structured internals of one probed turn (JSON-able)."""

    label: str
    query: str
    status: str = "?"
    answer: str = ""
    flow: str = "emergent"
    flow_score: float = 0.0
    path: dict = field(default_factory=dict)  # full {name, score, runner_up, emergent}
    lobes: list[dict] = field(default_factory=list)  # trace.lobes (activation rows)
    stages: list[dict] = field(default_factory=list)  # trace.flow_stages (+ steps)
    llm_calls: list[dict] = field(default_factory=list)  # per-hop ReAct capture
    tool_calls: list[dict] = field(default_factory=list)  # {name, input, output}
    usage: dict = field(default_factory=dict)
    meta_actions: list[dict] = field(default_factory=list)
    hints: list[dict] = field(default_factory=list)  # optimization hotspots (axis/target/reason)
    attention: dict = field(default_factory=dict)  # context tiers + per-stage funnel telemetry
    error: str | None = None

    @property
    def activated_lobes(self) -> list[str]:
        return [lb["id"] for lb in self.lobes if lb.get("activated")]

    def to_json(self) -> dict:
        return asdict(self)


async def probe(agent: Any, query: str, *, label: str = "") -> ProbeRecord:
    """Run one turn through ``agent`` and capture its internals.

    Never raises on a turn failure — the error is recorded so the report still
    renders what got that far.
    """
    rec = ProbeRecord(label=label or query[:48], query=query)
    result = None
    tool_in: dict[str, dict] = {}
    try:
        async for ev in agent.act(query):
            if isinstance(ev, ToolCall):
                tool_in[ev.id] = {"name": ev.name, "input": ev.input or {}}
            elif isinstance(ev, ToolResult):
                call = tool_in.get(ev.id, {"name": ev.name, "input": {}})
                rec.tool_calls.append({**call, "output": ev.output})
            elif isinstance(ev, Final):
                result = ev.result
    except Exception as exc:  # a crashed turn is still worth visualizing
        rec.status = "error"
        rec.error = f"{type(exc).__name__}: {exc}"
        return rec

    if result is not None:
        rec.status = result.status
        rec.answer = result.text
        rec.usage = result.usage.to_json()
        t = result.trace
        rec.path = dict(t.path)
        rec.flow = t.path.get("name", "emergent")
        rec.flow_score = t.path.get("score", 0.0)
        rec.lobes = t.lobes
        rec.stages = t.flow_stages
        rec.llm_calls = t.llm_calls
        rec.meta_actions = t.meta_actions
        rec.attention = getattr(t, "attention", {}) or {}
        # Optimization hotspots — the agent's own weight-patch proposals for this
        # turn (the viewer's "hotspots" function, kept inline in the report).
        suggest = getattr(agent, "suggest_optimizations", None)
        if callable(suggest):
            try:
                rec.hints = [o.to_json() for o in suggest()]
            except Exception:
                rec.hints = []
    return rec
