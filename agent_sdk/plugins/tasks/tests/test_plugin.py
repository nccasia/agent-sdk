"""Unit — TaskPlugin assembles its capability surface into an AgentSetup (no agent)."""

from __future__ import annotations

from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.tasks import TaskPlugin


def _setup():
    setup = AgentSetup()
    TaskPlugin().install(setup)
    return setup


def test_install_contributes_lobe_stages_flow_and_one_tool():
    setup = _setup()
    assert [lb.id for lb in setup.lobes] == ["task_rail"]
    assert {s.id for s in setup.stages} == {"plan", "execute", "deliver"}
    assert [f.id for f in setup.flows] == ["task"]
    # exactly one tool runtime — the single `todos` tool
    specs = [spec for t in setup.tools for spec in t.get_tool_specs()]
    assert [s["name"] for s in specs] == ["todos"]


def test_flow_recognizer_is_the_task_path():
    flow = _setup().flows[0]
    assert flow.signal({"query": "compute the total"}) == 0.9
    assert flow.signal({"query": "hello"}) == 0.0
