"""Integration — MetacognitionPlugin end-to-end through a real agent + the engine.

Pins the opt-in/parity contract and the next-turn flow-bias seam (a mid-turn bias is
persisted and routes the following turn). Delegation/fan-out is the planning plugin's
concern (see tests/test_planning.py), not metacognition's.
"""

from __future__ import annotations

from agent_sdk import PreactAgent
from agent_sdk.clients.fake import scripted
from agent_sdk.lobes.network import default_lobes, default_paths
from agent_sdk.plugins.metacognition import MetacognitionPlugin
from agent_sdk.session import SessionState


# ── opt-in / parity contract ────────────────────────────────────────────────────
def test_metacognition_lives_only_in_the_plugin():
    assert "meta" not in {p.name for p in default_paths()}
    assert not any(lb.id == "meta_context" for lb in default_lobes())
    bare = PreactAgent(client=scripted(lambda *a: "x"))  # no plugin
    assert "meta_control" not in {s["name"] for s in bare.engine.tools.get_tool_specs()}
    assert bare.inspect("rethink your approach to this").path[0] != "meta"


def test_plugin_adds_the_meta_lobes():
    agent = PreactAgent(client=scripted(lambda *a: "x"), plugins=[MetacognitionPlugin()])
    lobe_ids = {lb.id for lb in agent.engine.lobe_specs}
    bare_ids = {lb.id for lb in default_lobes()}
    # adds the meta mirror + the Navigator brief lobe, shifts nothing else
    assert lobe_ids - bare_ids == {"meta_context", "nav_brief"}


# ── next-turn flow bias ──────────────────────────────────────────────────────────
class _BiasModel:
    def __init__(self) -> None:
        self.biased = False

    def __call__(self, stage, system, messages, tools):
        if stage == "meta_reflect" and not self.biased:
            self.biased = True
            return {
                "tools": [
                    {"name": "meta_control", "input": {"action": "bias_flow", "path": "meta"}}
                ]
            }
        return "done"


async def test_bias_flow_persists_and_routes_next_turn():
    agent = PreactAgent(client=scripted(_BiasModel()), plugins=[MetacognitionPlugin()])
    state = SessionState()
    # turn 1 routes to meta (explicit cue) and the model records a bias toward `meta`
    await agent.engine.run("rethink the approach", state)
    assert state.meta_flow_bias == "meta"
    # turn 2 is a plain query that would NOT route to meta on its own — the recorded
    # bias (a deterministic signal folded into recognition) routes it there.
    assert agent.engine.inspect("hello there", state).path[0] == "meta"
