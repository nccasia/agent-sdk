"""Unit — the task pipeline stages, in isolation."""

from __future__ import annotations

from agent_sdk.plugins.tasks.stages import task_stages


def test_pipeline_shape():
    by_id = {s.id: s for s in task_stages()}
    assert set(by_id) == {"plan", "execute", "deliver"}
    # plan builds the rail (only the todos tool, agentic)
    assert by_id["plan"].loop == "agentic" and tuple(by_id["plan"].tools) == ("todos",)
    # execute is the generic per-todo map over the rail; open toolset for domain sub-agents
    assert by_id["execute"].loop == "map" and by_id["execute"].fanout_key == "todos"
    assert tuple(by_id["execute"].tools) == ()
    # both execute + deliver consult the render lobe
    assert "task_rail" in by_id["execute"].lobes and "task_rail" in by_id["deliver"].lobes
    assert by_id["deliver"].loop == "single"
