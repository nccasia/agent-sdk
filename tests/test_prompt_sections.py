"""A lobe can inject MULTIPLE prompt chunks, each its own master-prompt section.

The engine consumes ``Lobe.prompt(ctx) -> list[PromptContribution]`` (not just the static
``system_prompt``): every contribution becomes its own source-tagged, stage-filtered section
that the engine assembles (XML-wrapped) into the system prompt.
"""

from __future__ import annotations

from agent_sdk import PreactAgent, flow, stage
from agent_sdk.clients.fake import FakeClient
from agent_sdk.contracts.turn import PromptContribution, TurnContext
from agent_sdk.lobes.runtime import Lobe
from agent_sdk.network.activation import LAYER_COGNITION
from agent_sdk.session import SessionState


class _MultiSectionLobe(Lobe):
    id = "multi"
    name = "Multi"
    description = "Contributes several prompt sections."
    use_when = "demonstrating multi-section injection"
    layer = LAYER_COGNITION

    def activation(self, ctx: dict) -> float:
        return 1.0

    def prompt(self, _ctx: TurnContext) -> list[PromptContribution]:
        return [
            PromptContribution("ALPHA BLOCK", source="alpha", stability="stable"),
            PromptContribution("BETA BLOCK", source="beta", stability="volatile"),
            PromptContribution("ONLY-ON-S2", source="gamma", stage_ids=("s2",)),
        ]


class _MultiPlugin:
    name = "multi"

    def install(self, setup) -> None:
        setup.add_lobe(_MultiSectionLobe())
        setup.add_stage(stage("ms", lobes=["multi"], loop="single"))
        setup.add_flow(flow("ms", stages=["ms"], signal={"const": 1.0}))


def _engine():
    return PreactAgent(client=FakeClient(), plugins=[_MultiPlugin()], universal_memory=False).engine


def test_lobe_injects_multiple_sections():
    eng = _engine()
    st = eng.stage_registry.get("ms")
    sys, segs = eng._compose_system_segmented(
        st, {"query": "hi"}, SessionState(), [], TurnContext(query="hi")
    )
    # both unconditional chunks land as their own sections
    assert "ALPHA BLOCK" in sys and "BETA BLOCK" in sys
    assert "<alpha>" in sys and "<beta>" in sys  # each chunk → its own XML section, by `source`
    sources = {s["source"] for s in segs}
    assert {"alpha", "beta"} <= sources


def test_stage_ids_filter_a_chunk_to_its_stage():
    eng = _engine()
    st = eng.stage_registry.get("ms")  # id "ms" ≠ "s2"
    sys = eng._compose_system_segmented(
        st, {"query": "hi"}, SessionState(), [], TurnContext(query="hi")
    )[0]
    assert "ONLY-ON-S2" not in sys  # stage_ids=("s2",) → excluded from stage "ms"


def test_static_system_prompt_still_works_without_ctx():
    # no turn_ctx → fall back to the static system_prompt path (back-compat)
    eng = _engine()
    st = eng.stage_registry.get("ms")
    sys = eng._compose_system(st, {"query": "hi"}, SessionState(), [])
    # _MultiSectionLobe has no static system_prompt, so nothing from it — but no crash
    assert isinstance(sys, str)
