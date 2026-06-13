"""First-class Stage + builder + registry."""

from __future__ import annotations

from agent_sdk.activable import Activable
from agent_sdk.stages import Stage, StageRegistry, stage


def test_builder_defaults_always_on():
    s = stage("plan", lobes=["plan"])
    assert s.id == "plan"
    assert s.name == "plan"
    assert s.loop == "single"
    assert s.signal({}) == 1.0
    assert s.lobes == ("plan",)


def test_builder_with_signal_gates():
    s = stage(
        "research",
        lobes=["research"],
        loop="agentic",
        tools=["search"],
        signal=lambda ctx: 1.0 if ctx.get("needs_sources") else 0.0,
    )
    assert s.signal({"needs_sources": True}) == 1.0
    assert s.signal({}) == 0.0


def test_class_form_subclass():
    class Research(Stage):
        id, name = "research", "Research"
        description = "Gather evidence."
        use_when = "needs external facts"
        lobes = ("research",)
        loop = "agentic"
        tools = ("search",)

        def signal(self, ctx):
            return 1.0 if ctx.get("needs_sources") else 0.0

    r = Research()
    assert r.id == "research"
    assert r.lobes == ("research",)
    assert r.signal({"needs_sources": True}) == 1.0
    assert r.signal({}) == 0.0


def test_stage_is_activable():
    s = stage("plan", lobes=["plan"])
    assert isinstance(s, Activable)


def test_to_flow_step_bridges_runtime():
    s = stage(
        "research",
        lobes=["research"],
        loop="agentic",
        tools=["search"],
        signal=lambda ctx: 0.0,
        threshold=0.5,
        max_tokens=2048,
    )
    fs = s.to_flow_step()
    assert fs.name == "research"
    assert fs.loop == "agentic"
    assert fs.tools == ("search",)
    assert fs.max_tokens == 2048
    # the FlowStep's signals carry the stage's gating
    assert fs.signals({"x": 1})["research"] == 0.0
    assert fs.min_activation == 0.5


def test_map_loop_requires_fanout_key():
    s = stage("fanout", lobes=["plan"], loop="map", fanout_key="sub_questions")
    fs = s.to_flow_step()
    assert fs.fanout_key == "sub_questions"


def test_registry_resolve_by_reference():
    reg = StageRegistry([stage("plan", lobes=["plan"]), stage("synth", lobes=["synthesize"])])
    resolved = reg.resolve(["plan", "synth", "missing"])
    assert [s.id for s in resolved] == ["plan", "synth"]
    assert reg.get("plan").lobes == ("plan",)
    assert set(reg.ids()) == {"plan", "synth"}
