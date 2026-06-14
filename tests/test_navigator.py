"""Navigator layer — the thinking phase controller (advance / redo / goto + brief).

A post-stage hook decides "are we good to go?" against a phase's definition of done, picks what
runs next (advance · redo · goto · done), and can author the next phase's goal/instruction/DoD.
Covers: the pure ``_next_phase`` enactor (advance/redo/goto/done, budgets, pin safety, apply-gate),
the deterministic empty-output redo, the ``nav_brief`` lobe, the ``meta_control`` navigate enactor,
parity (off by default), and an end-to-end redo loop with history rewind.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_sdk import PreactAgent, probe
from agent_sdk.clients.fake import scripted
from agent_sdk.engine import _TURN
from agent_sdk.flow_def import flow as make_flow
from agent_sdk.memory.scratchpad import Scratchpad
from agent_sdk.metacognition_facade import Metacognition
from agent_sdk.plugins.metacognition.lobes import NavBriefLobe
from agent_sdk.plugins.metacognition.tool import (
    NAV_REQUEST_KEY,
    PHASE_BRIEF_KEY,
    MetaControlToolRuntime,
)
from agent_sdk.stages import stage as make_stage

# A 4-phase pipeline: two work phases then the pinned grounding pair.
_STAGES = [
    make_stage("a", lobes=["synthesize"], loop="single"),
    make_stage("b", lobes=["synthesize"], loop="single"),
    make_stage("cite", lobes=["cite"], loop="single"),
    make_stage("filter", lobes=["filter"], loop="single"),
]


def _engine(*, apply_nav: bool):
    actions = {"adjust_lobe_slice"}
    if apply_nav:
        actions |= {"redo_phase", "goto_phase"}
    agent = PreactAgent(
        client=scripted(lambda *a: "x"),
        instructions="bot",
        metacognition=Metacognition(mode="apply", apply_actions=actions),
    )
    return agent.engine


def _turn():
    return SimpleNamespace(scratchpad=Scratchpad(), lobe_outputs={}, stage_id="")


# ── the pure enactor: advance / redo / goto / done ────────────────────────────
def test_next_phase_advances_by_default():
    eng = _engine(apply_nav=True)
    nxt, rewind, action, _ = eng._next_phase(
        0, _STAGES, _turn(), _STAGES[0], "answer", {"a": 1}, redo_budget=1
    )
    assert (nxt, rewind, action) == (1, None, "advance")


def test_deterministic_redo_on_empty_output_then_budget_stops():
    eng = _engine(apply_nav=True)
    # empty output + under budget → redo this phase (rewind to it)
    nxt, rewind, action, _ = eng._next_phase(
        0, _STAGES, _turn(), _STAGES[0], "", {"a": 1}, redo_budget=1
    )
    assert (nxt, rewind, action) == (0, "a", "redo_phase")
    # budget exhausted (already redone) → advance
    nxt, rewind, action, _ = eng._next_phase(
        0, _STAGES, _turn(), _STAGES[0], "", {"a": 2}, redo_budget=1
    )
    assert (nxt, action) == (1, "advance")


def test_model_navigate_redo_and_goto():
    eng = _engine(apply_nav=True)
    turn = _turn()
    # model requests redo (one-shot)
    turn.scratchpad.set(NAV_REQUEST_KEY, {"to": "redo"})
    nxt, rewind, action, _ = eng._next_phase(
        1, _STAGES, turn, _STAGES[1], "answer", {"b": 1}, redo_budget=1
    )
    assert (nxt, rewind, action) == (1, "b", "redo_phase")
    assert turn.scratchpad.get(NAV_REQUEST_KEY) is None  # consumed
    # model requests goto backward → rewind to target
    turn.scratchpad.set(NAV_REQUEST_KEY, {"to": "a"})
    nxt, rewind, action, _ = eng._next_phase(
        1, _STAGES, turn, _STAGES[1], "answer", {"a": 1, "b": 1}, redo_budget=1
    )
    assert (nxt, rewind, action) == (0, "a", "goto_phase")


def test_pin_safety_done_runs_grounding_first():
    eng = _engine(apply_nav=True)
    turn = _turn()
    turn.scratchpad.set(NAV_REQUEST_KEY, {"to": "done"})
    # finishing after phase b, but cite/filter have not run → redirected to cite (index 2)
    nxt, rewind, action, reason = eng._next_phase(
        1, _STAGES, turn, _STAGES[1], "answer", {"a": 1, "b": 1}, redo_budget=1
    )
    assert nxt == 2 and "pinned" in reason


def test_nav_off_by_default_is_parity_advance():
    eng = _engine(apply_nav=False)  # redo_phase/goto_phase not in the apply set
    # even an empty phase advances (no redo) — default-network behavior is unchanged
    nxt, rewind, action, _ = eng._next_phase(
        0, _STAGES, _turn(), _STAGES[0], "", {"a": 1}, redo_budget=1
    )
    assert (nxt, action) == (1, "advance")


def test_next_phase_is_deterministic():
    eng = _engine(apply_nav=True)
    args = (0, _STAGES, _turn(), _STAGES[0], "", {"a": 1})
    assert eng._next_phase(*args, redo_budget=1) == eng._next_phase(*args, redo_budget=1)


# ── the nav_brief lobe (the "prepare the next phase" enactor) ──────────────────
def test_nav_brief_lobe_renders_goal_instruction_dod():
    turn = _turn()
    turn.stage_id = "b"
    turn.scratchpad.set(
        PHASE_BRIEF_KEY,
        {"b": {"goal": "tally revenue", "instruction": "use SQL", "dod": ["all 3 regions"]}},
    )
    block = NavBriefLobe().prompt(turn)
    assert block and "## Navigator brief" in block[0].text
    assert "tally revenue" in block[0].text and "use SQL" in block[0].text
    assert "all 3 regions" in block[0].text
    # no brief for this phase ⇒ no contribution
    assert NavBriefLobe().prompt(_turn()) == []


# ── the meta_control navigate enactor ─────────────────────────────────────────
async def _call(turn, inp):
    tok = _TURN.set(turn)
    try:
        return await MetaControlToolRuntime().call_tool("meta_control", inp)
    finally:
        _TURN.reset(tok)


async def test_navigate_writes_request_and_brief():
    turn = _turn()
    out = await _call(
        turn,
        {"action": "navigate", "to": "redo", "goal": "redo properly", "dod": ["cite sources"]},
    )
    assert turn.scratchpad.get(NAV_REQUEST_KEY) == {"to": "redo", "reason": "navigate"}
    # redo/next brief is keyed under "next"
    assert turn.scratchpad.get(PHASE_BRIEF_KEY)["next"]["goal"] == "redo properly"
    assert "redo" in out


async def test_navigate_refuses_pinned_target():
    turn = _turn()
    out = await _call(turn, {"action": "navigate", "to": "cite"})
    assert "Refused" in out and turn.scratchpad.get(NAV_REQUEST_KEY) is None


# ── end-to-end: a phase that misses its DoD is redone, then the turn completes ──
async def test_end_to_end_redo_then_complete():
    calls = {"a": 0}

    def model(sid, sy, m, t):
        if sid == "a":
            calls["a"] += 1
            return "" if calls["a"] == 1 else "phase a done"  # empty first → redo
        return f"{sid} ok"

    solve = make_flow(
        "solve", stages=["a", "cite", "filter"], grounds=False, threshold=0.5,
        signal=lambda ctx: 1.0,
    )
    agent = PreactAgent(
        client=scripted(model),
        instructions="bot",
        stages=[_STAGES[0], _STAGES[2], _STAGES[3]],  # a + pinned cite/filter
        flows=[solve],
        metacognition=Metacognition(mode="apply", apply_actions={"redo_phase"}),
    )
    rec = await probe(agent, "do it", label="t")
    stages_run = [s.get("stage") for s in rec.stages]
    # phase "a" ran twice (empty → redo → ok), then the pinned grounding pair
    assert stages_run.count("a") == 2
    assert "cite" in stages_run and "filter" in stages_run
    assert rec.status == "answered"
    assert calls["a"] == 2  # exactly one redo (budget = 1)
