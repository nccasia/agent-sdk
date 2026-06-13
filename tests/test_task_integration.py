"""Integration — the TaskPlugin end-to-end through a real agent + the engine.

Mounts TaskPlugin on a PreactAgent and drives a turn with a scripted model: the `plan`
stage builds the rail via the `todos` tool → the engine's generic `loop="map"` runs one
sub-execution per todo → the `deliver` stage states the answer (with the rail rendered by
the task_rail lobe). Also pins the opt-in contract: task lives ONLY in the plugin.
"""

from __future__ import annotations

from agent_sdk import PreactAgent, probe
from agent_sdk.clients.fake import scripted
from agent_sdk.lobes.network import default_lobes, default_paths
from agent_sdk.plugins import TaskPlugin


class _Model:
    """plan: add a 2-step rail; execute: per-item result; deliver: final answer."""

    def __init__(self) -> None:
        self.added = 0

    def __call__(self, stage_id, system, messages, tools):
        if stage_id == "plan":
            if self.added < 2:
                self.added += 1
                return {"tools": [{"name": "todos",
                                   "input": {"action": "add", "title": f"step {self.added}"}}]}
            return "planned"
        if stage_id == "deliver":
            return "FINAL ANSWER: 42"
        return "sub-result"  # execute, per item


# ── opt-in contract ───────────────────────────────────────────────────────────
def test_task_lives_only_in_the_plugin():
    assert "task" not in {p.name for p in default_paths()}
    assert not any("task" in lb.id for lb in default_lobes())
    bare = PreactAgent(client=scripted(lambda *a: "x"))  # no plugin
    assert "todos" not in {s["name"] for s in bare.engine.tools.get_tool_specs()}
    assert bare.inspect("compute the total revenue").path[0] != "task"


# ── end-to-end pipeline ─────────────────────────────────────────────────────────
async def test_plugin_drives_plan_then_per_todo_then_deliver():
    agent = PreactAgent(client=scripted(_Model()), plugins=[TaskPlugin()])
    assert agent.inspect("compute the total and list the top items").path[0] == "task"
    rec = await probe(agent, "compute the total and list the top items", label="t")

    adds = [tc for tc in rec.tool_calls
            if tc["name"] == "todos" and (tc.get("input") or {}).get("action") == "add"]
    assert len(adds) == 2  # plan built a 2-step rail via the one todos tool
    exec_calls = [c for c in agent.client.calls if c["stage"] == "execute"]
    assert len(exec_calls) == 2  # the engine ran ONE sub-execution per todo
    # the task_rail lobe rendered the checklist into the deliver prompt
    deliver = [c for c in agent.client.calls if c["stage"] == "deliver"]
    assert deliver and "Task checklist" in deliver[0]["system"]
    assert rec.status == "answered" and "FINAL ANSWER" in rec.answer
