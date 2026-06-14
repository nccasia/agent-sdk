"""Unit — the meta_context render lobe, in isolation (a fake TurnContext, no engine)."""

from __future__ import annotations

from types import SimpleNamespace

from agent_sdk.plugins.metacognition.lobes import MetaContextLobe


def _ctx(*, path=None, stage=None, active=frozenset(), outs=None):
    return SimpleNamespace(
        active_path=path, stage_id=stage, active_lobes=active, lobe_outputs=outs or {}
    )


def test_renders_thinking_state_block():
    ctx = _ctx(
        path="research",
        stage="meta_reflect",
        active=frozenset({"synthesize", "meta_context"}),
        outs={
            "skills_in_use": ["triage"],
            "meta_observations": [{"kind": "low_confidence_path", "target": "research"}],
            "meta_flow_bias": "qna",
        },
    )
    out = MetaContextLobe().prompt(ctx)
    assert len(out) == 1
    text = out[0].text
    assert "How you are thinking" in text
    assert "Path (recognized intent): research" in text
    assert "Current step: meta_reflect" in text
    assert "Skills in use: triage" in text
    assert "low_confidence_path @ research" in text
    assert "applies to your NEXT turn" in text
    assert out[0].source == "meta_context"


def test_empty_state_contributes_nothing():
    assert MetaContextLobe().prompt(_ctx()) == []
    # lobe_outputs absent entirely is still harmless
    assert MetaContextLobe().prompt(SimpleNamespace()) == []


def test_activation_is_always_on():
    assert MetaContextLobe().activation({}) == 1.0
