"""Unit — the meta_control tool's enactors write the right turn-state keys + pin guards.

Each action follows reason → write → enact: the tool only WRITES a decision; the engine /
existing lobes read it. Here we assert the WRITE half against a fake turn (no engine).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_sdk.engine import _TURN
from agent_sdk.memory.scratchpad import Scratchpad
from agent_sdk.plugins.metacognition.tool import MetaControlToolRuntime


def _turn():
    return SimpleNamespace(lobe_outputs={}, scratchpad=Scratchpad())


async def _call(turn, inp):
    token = _TURN.set(turn)
    try:
        return await MetaControlToolRuntime().call_tool("meta_control", inp)
    finally:
        _TURN.reset(token)


async def test_use_skills_writes_skills_in_use_and_strips_pinned():
    turn = _turn()
    out = await _call(turn, {"action": "use_skills", "slugs": ["triage", "cite", "filter"]})
    assert turn.lobe_outputs["skills_in_use"] == ["triage"]  # cite/filter stripped (pin guard)
    assert "triage" in out


async def test_use_skills_requires_real_slugs():
    turn = _turn()
    out = await _call(turn, {"action": "use_skills", "slugs": ["cite"]})
    assert "Error" in out
    assert "skills_in_use" not in turn.lobe_outputs


async def test_bias_flow_records_next_turn_bias():
    turn = _turn()
    out = await _call(turn, {"action": "bias_flow", "path": "research"})
    assert turn.lobe_outputs["meta_flow_bias"] == "research"
    assert "NEXT turn" in out


async def test_fan_out_writes_worklist_to_scratchpad():
    turn = _turn()
    items = [{"label": "a", "input": "do a", "lobes": ["meta_context"]}, {"input": "do b"}]
    out = await _call(turn, {"action": "fan_out", "items": items})
    assert turn.scratchpad.get("meta_fanout") == items
    assert "2 sub-task" in out


async def test_regulate_skip_records_request():
    turn = _turn()
    out = await _call(turn, {"action": "regulate", "request": "skip", "step": "meta_fanout"})
    assert turn.scratchpad.get("meta_regulate_request") == {
        "request": "skip",
        "step": "meta_fanout",
    }
    assert "recorded" in out


async def test_regulate_never_skips_a_pinned_step():
    turn = _turn()
    out = await _call(turn, {"action": "regulate", "request": "skip", "step": "cite"})
    assert "Refused" in out
    assert turn.scratchpad.get("meta_regulate_request") is None


async def test_unknown_action_errors():
    turn = _turn()
    out = await _call(turn, {"action": "nonsense"})
    assert "unknown action" in out


async def test_no_turn_is_handled():
    # no _TURN set ⇒ the tool reports cleanly instead of crashing
    rt = MetaControlToolRuntime()
    out = await rt.call_tool("meta_control", {"action": "bias_flow", "path": "x"})
    assert "no active turn" in out


@pytest.mark.parametrize("name", ["todos", "search"])
async def test_rejects_other_tool_names(name):
    assert (await MetaControlToolRuntime().call_tool(name, {})) == f"Error: unknown tool {name!r}."
