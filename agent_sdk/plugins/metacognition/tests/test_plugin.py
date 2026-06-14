"""Unit — MetacognitionPlugin assembles its capacity surface into an AgentSetup (no agent)."""

from __future__ import annotations

from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.metacognition import MetacognitionPlugin
from agent_sdk.plugins.metacognition.path import bias_flag


def _setup(**kw):
    setup = AgentSetup()
    MetacognitionPlugin(**kw).install(setup)
    return setup


def test_install_contributes_lobe_stages_flow_and_one_tool():
    setup = _setup()
    assert [lb.id for lb in setup.lobes] == ["meta_context", "nav_brief"]
    assert {s.id for s in setup.stages} == {"meta_reflect"}
    assert [f.id for f in setup.flows] == ["meta"]
    # a single stateful tool runtime — the one `meta_control` tool (mounted priority)
    assert setup.tools == []
    specs = [spec for rt in setup.tool_runtimes for spec in rt.get_tool_specs()]
    assert [s["name"] for s in specs] == ["meta_control"]


def test_flow_false_omits_the_flow_but_keeps_lobe_stages_tool():
    setup = _setup(flow=False)
    assert setup.flows == []
    assert [lb.id for lb in setup.lobes] == ["meta_context", "nav_brief"]
    assert {s.id for s in setup.stages} == {"meta_reflect"}


def test_recognizer_is_conservative_and_reads_next_turn_bias():
    flow = _setup().flows[0]
    assert flow.signal({"query": "rethink your approach to this"}) == 0.85
    assert flow.signal({"query": "what is the capital of France?"}) == 0.0
    # a recorded next-turn flow bias toward `meta` fires the recognizer deterministically
    assert flow.signal({"query": "hello", bias_flag("meta"): True}) == 1.0
