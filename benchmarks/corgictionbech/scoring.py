"""corgictionbech scoring — deterministic plugin-surface checks + the live reducers.

Split from ``run.py`` so the check logic is unit-importable. Two families:

- ``plugin_surface_checks`` — deterministic (no provider): the ``MetacognitionPlugin`` assembles
  its surface and its tool enactors write the right turn-state keys; ``cite``/``filter`` are never
  reshapeable. This is what makes the bench *match the implementation*, not just the kernel.
- the live reducers (``decision_quality`` / ``answered_correctly`` / ``live_metrics``) — read a
  probe record of the EQUIPPED agent (the best configuration) and judge whether the metacognition
  module made the expected skill/flow/fan-out choice and answered correctly. Single-arm: we measure
  the equipped agent, not a with-vs-without comparison.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from agent_sdk.memory.scratchpad import Scratchpad
from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.metacognition import MetacognitionPlugin
from agent_sdk.plugins.metacognition.tool import MetaControlToolRuntime


def _ck(cid: str, ok: bool, detail: str) -> dict:
    return {"id": cid, "ok": bool(ok), "detail": detail}


# ── deterministic: the plugin surface + tool enactors match the implementation ──────────────────
def _fake_turn() -> SimpleNamespace:
    return SimpleNamespace(lobe_outputs={}, scratchpad=Scratchpad())


def _call(turn: Any, inp: dict) -> str:
    from agent_sdk.engine import _TURN

    token = _TURN.set(turn)
    try:
        return asyncio.run(MetaControlToolRuntime().call_tool("meta_control", inp))
    finally:
        _TURN.reset(token)


def plugin_surface_checks() -> list[dict]:
    setup = AgentSetup()
    MetacognitionPlugin().install(setup)
    lobe_ids = [lb.id for lb in setup.lobes]
    stage_ids = {s.id for s in setup.stages}
    flow_ids = [f.id for f in setup.flows]
    tool_names = [spec["name"] for rt in setup.tool_runtimes for spec in rt.get_tool_specs()]

    checks = [
        _ck("surface.lobe", lobe_ids == ["meta_context"], f"lobes={lobe_ids}"),
        _ck("surface.stages", stage_ids == {"meta_reflect", "meta_fanout"}, f"stages={sorted(stage_ids)}"),
        _ck("surface.flow", flow_ids == ["meta"], f"flows={flow_ids}"),
        _ck("surface.tool", tool_names == ["meta_control"], f"tools={tool_names}"),
    ]

    # enactors write the right turn-state keys (reason → write → enact)
    t = _fake_turn()
    _call(t, {"action": "use_skills", "slugs": ["triage", "cite"]})
    checks.append(_ck("enact.skills_write", t.lobe_outputs.get("skills_in_use") == ["triage"],
                      "use_skills writes skills_in_use and strips pinned cite/filter"))
    t = _fake_turn()
    _call(t, {"action": "bias_flow", "path": "research"})
    checks.append(_ck("enact.flow_write", t.lobe_outputs.get("meta_flow_bias") == "research",
                      "bias_flow records the next-turn flow bias"))
    t = _fake_turn()
    _call(t, {"action": "fan_out", "items": [{"input": "a"}, {"input": "b"}]})
    checks.append(_ck("enact.fanout_write", len(t.scratchpad.get("meta_fanout") or []) == 2,
                      "fan_out writes the work-list to scratchpad"))
    t = _fake_turn()
    out = _call(t, {"action": "regulate", "request": "skip", "step": "cite"})
    checks.append(_ck("enact.pinned_never_skipped",
                      "Refused" in out and t.scratchpad.get("meta_regulate_request") is None,
                      "a grounding step (cite/filter) is never a meta skip decision"))
    return checks


# ── live (single-arm): read a probe record of the equipped agent ──────────────────────────────────
def _meta_calls(rec: Any) -> list[dict]:
    return [tc.get("input") or {} for tc in getattr(rec, "tool_calls", []) if tc.get("name") == "meta_control"]


def decision_quality(rec: Any, expect: dict) -> tuple[bool, str]:
    """Did the equipped arm pick the expected meta lever (skills / flow bias / fan-out)?"""
    calls = _meta_calls(rec)
    if "skills" in expect:
        want = set(expect["skills"])
        got = {s for c in calls if c.get("action") == "use_skills" for s in (c.get("slugs") or [])}
        return (want <= got, f"skills want⊆{sorted(want)} got={sorted(got)}")
    if "flow_bias" in expect:
        biases = {c.get("path") for c in calls if c.get("action") == "bias_flow"}
        return (expect["flow_bias"] in biases, f"flow_bias want={expect['flow_bias']} got={sorted(biases)}")
    if expect.get("fanout"):
        fanned = any(c.get("action") == "fan_out" and (c.get("items") or []) for c in calls)
        return (fanned, f"fan_out={'yes' if fanned else 'no'}")
    # no specific lever expected ⇒ quality is just "did it use the tool at all"
    return (bool(calls), f"meta_calls={len(calls)}")


def answered_correctly(rec: Any, expect: dict) -> bool:
    want = expect.get("answer_contains")
    if not want:
        return getattr(rec, "status", "") == "answered"
    return want.lower() in (getattr(rec, "answer", "") or "").lower()


def _tokens(rec: Any) -> int:
    u = getattr(rec, "usage", {}) or {}
    return int(u.get("total_tokens") or u.get("input_tokens", 0) + u.get("output_tokens", 0) or 0)


def live_metrics(rows: list[dict]) -> dict:
    """rows: [{correct, meta_tokens}] for the equipped agent (one per scenario)."""
    n = len(rows) or 1
    return {
        "accuracy": round(sum(1 for r in rows if r["correct"]) / n, 3),
        "meta_tokens_avg": round(sum(r["meta_tokens"] for r in rows) / n, 1),
    }


def live_row(rec: Any, expect: dict) -> dict:
    return {"correct": answered_correctly(rec, expect), "meta_tokens": _tokens(rec)}
