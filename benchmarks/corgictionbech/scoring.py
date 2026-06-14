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

    # The shipped surface (matches the implementation): the meta_context mirror + the nav_brief
    # lobe, the meta_reflect stage, the meta flow, and the single meta_control tool. Asserted as
    # "contains" so the bench is resilient to the surface growing — it gates the contract, not an
    # exact set. (Metacognition reshapes the CURRENT approach: use_skills / bias_flow / regulate /
    # navigate. Delegation/fan-out is a SEPARATE concern — the planning plugin — not a meta action.)
    lset, sset = set(lobe_ids), set(stage_ids)
    checks = [
        _ck("surface.lobes", {"meta_context", "nav_brief"} <= lset, f"lobes={lobe_ids}"),
        _ck("surface.stage", "meta_reflect" in sset, f"stages={sorted(sset)}"),
        _ck("surface.flow", "meta" in flow_ids, f"flows={flow_ids}"),
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
    _call(t, {"action": "navigate", "to": "redo", "reason": "the step produced nothing"})
    nav = t.scratchpad.get("nav_request") or {}
    checks.append(_ck("enact.navigate_write", nav.get("to") == "redo",
                      "navigate records the phase-cursor request (redo/goto/done)"))
    t = _fake_turn()
    out = _call(t, {"action": "regulate", "request": "skip", "step": "cite"})
    checks.append(_ck("enact.pinned_never_skipped",
                      "Refused" in out and t.scratchpad.get("meta_regulate_request") is None,
                      "a grounding step (cite/filter) is never a meta skip decision"))
    t = _fake_turn()
    nav_out = _call(t, {"action": "navigate", "to": "cite"})
    checks.append(_ck("enact.navigate_never_targets_pinned",
                      "Refused" in nav_out and not t.scratchpad.get("nav_request"),
                      "navigate cannot target a pinned grounding step"))
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
    """rows: [{correct, meta_fired, category, meta_tokens}] for the EQUIPPED agent on the hard set.

    Headline = ``solve_rate`` (did metacognition + the flow actually solve the hard problem) and
    ``meta_engagement`` (how often the agent reshaped its thinking via meta_control). Per-category
    solve-rates surface WHICH kinds of complex problems it handles."""
    n = len(rows) or 1
    cats: dict[str, list[bool]] = {}
    for r in rows:
        cats.setdefault(r.get("category", "?"), []).append(bool(r["correct"]))
    by_cat = {c: round(sum(v) / len(v), 2) for c, v in cats.items()}
    return {
        "solve_rate": round(sum(1 for r in rows if r["correct"]) / n, 3),
        "meta_engagement": round(sum(1 for r in rows if r.get("meta_fired")) / n, 3),
        "meta_tokens_avg": round(sum(r["meta_tokens"] for r in rows) / n, 1),
        "by_category": by_cat,
    }


def live_row(rec: Any, expect: dict, category: str = "?") -> dict:
    return {
        "correct": answered_correctly(rec, expect),
        "meta_fired": bool(_meta_calls(rec)),
        "category": category,
        "meta_tokens": _tokens(rec),
    }
