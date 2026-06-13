"""End-to-end of the clean split: the `todos` tool (domain) publishes a work-list to
turn state; the engine's GENERIC `loop="map"` fans out one scoped sub-execution per item
(its own prompt/tools via the item spec), with state carry. No task logic in the engine."""

from __future__ import annotations

from agent_sdk import PreactAgent, flow, probe, stage, tool
from agent_sdk.clients.fake import scripted
from agent_sdk.plugins.tasks import TodosToolRuntime


@tool
async def work(x: int = 0) -> str:
    "A trivial work tool."
    return "worked"


class _Model:
    """plan: build a 2-step rail via the todos tool; execute: per-item; deliver: finish."""

    def __init__(self) -> None:
        self.added = 0

    def __call__(self, stage_id, system, messages, tools):
        if stage_id == "plan":
            if self.added == 0:
                self.added += 1
                return {"tools": [{"name": "todos", "input": {"action": "add", "title": "step A"}}]}
            if self.added == 1:
                self.added += 1
                return {"tools": [{"name": "todos", "input": {
                    "action": "add", "title": "step B", "deps": ["t0"], "system_prompt": "FOCUS-B"}}]}
            return "planned"
        if stage_id == "deliver":
            return "FINAL: done"
        last = str(messages[-1]["content"]) if messages else ""
        return ("did " + last.split(":", 1)[-1].strip()) if "Sub-task" in last else "ok"


def _agent():
    rt = TodosToolRuntime(fanout_key="todos")
    agent = PreactAgent(
        client=scripted(_Model()),
        instructions="bot",
        tools=[rt, work],
        flows=[flow("task", stages=["plan", "execute", "deliver"], signal={"const": 1.0})],
        stages=[
            stage("plan", lobes=["synthesize"], loop="agentic", tools=["todos"], hops=6),
            stage("execute", lobes=["synthesize"], loop="map", fanout_key="todos",
                  tools=["work"], hops=4),
            stage("deliver", lobes=["synthesize"], loop="single"),
        ],
    )
    return agent, rt


async def test_plan_builds_rail_then_map_runs_each_item():
    agent, rt = _agent()
    rec = await probe(agent, "do the task", label="t")
    # the plan stage built a 2-step rail via the one todos tool
    assert [t.title for t in rt.rail.todos] == ["step A", "step B"]
    # the engine's generic map ran ONE sub-execution per item (dependency order)
    exec_calls = [c for c in agent.client.calls if c["stage"] == "execute"]
    assert len(exec_calls) == 2
    assert rec.status == "answered" and "FINAL" in rec.answer
    # the per-item spec was honored: step B's sub-execution carried its system_prompt
    assert any("FOCUS-B" in c["system"] for c in exec_calls)


async def test_state_carries_across_items():
    agent, _ = _agent()
    await probe(agent, "go", label="t")
    # step B's prompt includes step A's result (carry via notes)
    b = [c for c in agent.client.calls if c["stage"] == "execute" and "t1" in str(c["messages"])]
    assert b and "t0" in b[0]["system"]


async def test_map_without_worklist_degrades_to_single_run():
    # a map stage with nothing in scratchpad[fanout_key] → one normal agentic run (parity)
    agent = PreactAgent(
        client=scripted(lambda s, sy, m, t: "answer"),
        instructions="bot", tools=[work],
        flows=[flow("q", stages=["solo"], signal={"const": 1.0})],
        stages=[stage("solo", lobes=["synthesize"], loop="map", fanout_key="todos", tools=["work"])],
    )
    rec = await probe(agent, "go", label="t")
    assert rec.status == "answered"
