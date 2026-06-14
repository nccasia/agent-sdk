"""Planning capability (docs 08 + 12) — plan-driven fan-out: plan → supervise → fanout → fanin.

The agent plans with `TodoWrite` (each todo a designed step); a deterministic supervisor picks the
execution structure from the plan's deps (fanout when independent, sequential when dependent); the
engine runs one subagent per todo; a fan-in step aggregates. Covers: the tool writes the list; the
lobes render plan / structure / results; the supervisor's decision; complex queries route to the
`plan` flow; and end-to-end fan-out (independent) + sequential (deps-bearing) turns.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_sdk import PreactAgent, probe
from agent_sdk.clients.fake import scripted
from agent_sdk.engine import _TURN
from agent_sdk.memory.scratchpad import Scratchpad
from agent_sdk.plugins.planning import PlanningPlugin
from agent_sdk.plugins.planning.lobes import (
    RESULTS_KEY,
    STRUCTURE_KEY,
    PlanResultsLobe,
    PlanSuperviseLobe,
    TodoListLobe,
)
from agent_sdk.plugins.planning.path import complexity_score
from agent_sdk.plugins.planning.tool import TODOS_KEY, TodoWriteToolRuntime


# ── the TodoWrite tool + todo_list lobe ───────────────────────────────────────
async def _call(turn, inp):
    tok = _TURN.set(turn)
    try:
        return await TodoWriteToolRuntime().call_tool("TodoWrite", inp)
    finally:
        _TURN.reset(tok)


def _turn():
    return SimpleNamespace(scratchpad=Scratchpad(), lobe_outputs={})


async def test_todowrite_writes_the_list():
    turn = _turn()
    out = await _call(
        turn,
        {
            "todos": [
                {
                    "content": "Compute revenue by region",
                    "status": "in_progress",
                    "activeForm": "Computing revenue by region",
                },
                {"content": "Find top products", "status": "pending"},
            ]
        },
    )
    todos = turn.scratchpad.as_list(TODOS_KEY)
    assert [t["content"] for t in todos] == ["Compute revenue by region", "Find top products"]
    assert todos[0]["status"] == "in_progress"
    assert "Computing revenue by region" in out and "0/2 done" in out


async def test_todowrite_preserves_step_design_and_deps():
    turn = _turn()
    await _call(
        turn,
        {
            "todos": [
                {"content": "A", "status": "pending", "prompt": "do A", "tools": ["sql"]},
                {"content": "B", "status": "pending", "deps": [1]},
            ]
        },
    )
    todos = turn.scratchpad.as_list(TODOS_KEY)
    assert todos[0]["prompt"] == "do A" and todos[0]["tools"] == ["sql"]
    assert todos[1]["deps"] == [1]


async def test_todowrite_requires_todos():
    turn = _turn()
    out = await _call(turn, {"todos": []})
    assert "Error" in out and not turn.scratchpad.as_list(TODOS_KEY)


def test_todo_list_lobe_renders_the_plan():
    turn = _turn()
    turn.scratchpad.set(
        TODOS_KEY,
        [
            {"content": "A", "status": "completed", "activeForm": "Doing A"},
            {"content": "B", "status": "in_progress", "activeForm": "Doing B"},
        ],
    )
    block = TodoListLobe().prompt(turn)
    assert block and "1/2 done" in block[0].text
    assert "[x] A" in block[0].text and "[~] Doing B" in block[0].text
    # empty list ⇒ no contribution
    assert TodoListLobe().prompt(_turn()) == []


# ── the supervisor's structure decision (deterministic) ───────────────────────
def test_supervise_lobe_picks_fanout_for_designed_independent_steps():
    turn = _turn()
    turn.scratchpad.set(
        TODOS_KEY,
        [
            {"content": "A", "status": "pending", "prompt": "do A", "tools": ["sql"]},
            {"content": "B", "status": "pending", "prompt": "do B"},
        ],
    )
    block = PlanSuperviseLobe().prompt(turn)
    assert turn.scratchpad.get(STRUCTURE_KEY) == "fanout"
    assert block and "fanout" in block[0].text


def test_supervise_lobe_picks_inline_for_small_plain_plan():
    turn = _turn()
    turn.scratchpad.set(
        TODOS_KEY,
        [{"content": "A", "status": "pending"}, {"content": "B", "status": "pending"}],
    )
    block = PlanSuperviseLobe().prompt(turn)
    # a small (2-step) plain independent plan → the main agent works the list itself
    assert turn.scratchpad.get(STRUCTURE_KEY) == "inline"
    assert block and "inline" in block[0].text


def test_supervise_lobe_fans_out_many_plain_steps():
    turn = _turn()
    turn.scratchpad.set(
        TODOS_KEY,
        [{"content": c, "status": "pending"} for c in ("A", "B", "C")],
    )
    PlanSuperviseLobe().prompt(turn)
    # ≥3 independent pieces → fan out, even without per-step design (parallelism pays off)
    assert turn.scratchpad.get(STRUCTURE_KEY) == "fanout"


def test_supervise_lobe_picks_sequential_when_deps_present():
    turn = _turn()
    turn.scratchpad.set(
        TODOS_KEY,
        [
            {"content": "A", "status": "pending"},
            {"content": "B", "status": "pending", "deps": [1]},
        ],
    )
    PlanSuperviseLobe().prompt(turn)
    assert turn.scratchpad.get(STRUCTURE_KEY) == "sequential"
    # a 0/1-step plan is left unsupervised (runs as a single sub-execution)
    solo = _turn()
    solo.scratchpad.set(TODOS_KEY, [{"content": "A", "status": "pending"}])
    assert PlanSuperviseLobe().prompt(solo) == [] and solo.scratchpad.get(STRUCTURE_KEY) is None


def test_plan_results_lobe_renders_subagent_results():
    turn = _turn()
    turn.scratchpad.set(
        RESULTS_KEY,
        [
            {"label": "A", "result": "ra", "status": "ok"},
            {"label": "B", "error": "boom", "status": "failed"},
        ],
    )
    block = PlanResultsLobe().prompt(turn)
    assert block and "### A" in block[0].text and "ra" in block[0].text
    assert "### B (failed)" in block[0].text and "boom" in block[0].text
    assert PlanResultsLobe().prompt(_turn()) == []


# ── the routing decision (complexity recognizer) ──────────────────────────────
def test_complexity_recognizer_precision_and_recall():
    delegate = [
        "Compare the GDP, population, and land area of Canada, Australia, and Brazil.",
        "For each of Python, Rust, and Go, name one strength and one use case.",
        "Research three renewable sources — solar, wind, and hydro — and give one advantage of each.",
        "What are the capitals of France, Germany, Italy, and Spain?",
    ]
    single = [
        "What is the capital of France?",
        "What is 2 + 2?",
        "What is the capital and currency of Japan?",
        "Compare apples and oranges.",
        "List three prime numbers.",
    ]
    assert all(complexity_score(q) >= 0.5 for q in delegate)
    assert all(complexity_score(q) < 0.5 for q in single)


def test_complex_routes_to_plan_simple_does_not():
    agent = PreactAgent(
        client=scripted(lambda *a: "ok"), instructions="bot", plugins=[PlanningPlugin()]
    )
    assert (
        agent.inspect(
            "Compare the GDP, population, and area of Canada, Australia, and Brazil."
        ).path[0]
        == "plan"
    )
    assert agent.inspect("What is the capital of France?").path[0] != "plan"


# ── end-to-end: plan → supervise → fanout (one subagent per todo) → fanin ──────
def _three_step_planner(*, deps: bool):
    """A scripted model: plan 3 todos in the `plan` stage, answer each in `execute`, combine in
    `fanin`. With ``deps=True`` the todos chain (→ supervisor picks sequential)."""

    def model(sid, sy, m, t):
        last = str(m[-1]["content"]) if m else ""
        if sid == "plan":
            if "Todos updated" in last:  # plan already written ⇒ stop the agentic loop
                return "Plan ready."
            todos = []
            for i, name in enumerate(("alpha", "beta", "gamma"), start=1):
                todo = {"content": f"do {name}", "status": "pending", "prompt": f"Handle {name}."}
                if deps and i > 1:
                    todo["deps"] = [i - 1]
                todos.append(todo)
            return {"tools": [{"name": "TodoWrite", "input": {"todos": todos}}]}
        if sid == "execute":  # one subagent per todo — answer its sub-task
            return f"RESULT[{last.split(': ', 1)[-1]}]"
        return "Combined: alpha+beta+gamma."  # fanin / grounding

    return model


async def test_independent_plan_fans_out_one_subagent_per_todo():
    agent = PreactAgent(
        client=scripted(_three_step_planner(deps=False)),
        instructions="bot",
        plugins=[PlanningPlugin()],
    )
    rec = await probe(
        agent, "Compare alpha, beta, and gamma across these three dimensions.", label="t"
    )
    assert rec.flow == "plan"
    stages = [s.get("stage") for s in rec.stages]
    assert stages[:4] == ["plan", "supervise", "execute", "fanin"]
    # the supervisor chose fanout; one subagent ran per todo → three results captured
    assert rec.blackboard.get(STRUCTURE_KEY) == "fanout"
    results = rec.blackboard.get(RESULTS_KEY, [])
    assert len(results) == 3 and all(r["status"] == "ok" for r in results)
    assert rec.status == "answered" and "Combined" in rec.answer
    # each todo ran as a subagent with its OWN prompt + timeline (the viewer's Subagents panel)
    execute = next(s for s in rec.stages if s.get("stage") == "execute")
    subs = execute.get("subagents", [])
    assert len(subs) == 3
    assert all(sa["system_prompt"] and sa["steps"] for sa in subs)
    assert all("Handle" in sa["system_prompt"] for sa in subs)  # the per-todo prompt was applied


async def test_small_plain_plan_runs_inline_in_main_stage():
    """A small (2-step) plain independent plan → supervisor picks inline → the main agent works the
    list itself in the execute stage (no subagents spawned), and the flow still completes."""

    def model(sid, sy, m, t):
        last = str(m[-1]["content"]) if m else ""
        if sid == "plan":
            if "Todos updated" in last:
                return "Plan ready."
            todos = [{"content": f"do {n}", "status": "pending"} for n in ("alpha", "beta")]
            return {"tools": [{"name": "TodoWrite", "input": {"todos": todos}}]}
        # execute runs inline (one agentic loop over the plan) → no per-todo sub-task messages
        return "Combined inline: alpha+beta."

    agent = PreactAgent(client=scripted(model), instructions="bot", plugins=[PlanningPlugin()])
    rec = await probe(agent, "Compare alpha and beta, and also summarize both together.", label="t")
    assert rec.flow == "plan"
    assert rec.blackboard.get(STRUCTURE_KEY) == "inline"
    # inline ⇒ NO subagents spawned; the main agent solved the pieces in its own stage
    assert not rec.blackboard.get(RESULTS_KEY)
    execute = next(s for s in rec.stages if s.get("stage") == "execute")
    assert execute.get("subagents", []) == []
    assert rec.status == "answered" and "alpha" in rec.answer


async def test_deps_plan_runs_sequentially():
    agent = PreactAgent(
        client=scripted(_three_step_planner(deps=True)),
        instructions="bot",
        plugins=[PlanningPlugin()],
    )
    rec = await probe(
        agent, "Analyze alpha, beta, and gamma in order, each step building on the previous.",
        label="t",
    )
    assert rec.flow == "plan"
    # the deps-bearing plan was supervised to 'sequential'
    assert rec.blackboard.get(STRUCTURE_KEY) == "sequential"
    results = rec.blackboard.get(RESULTS_KEY, [])
    assert len(results) == 3 and rec.status == "answered"
