"""Integration — MetacognitionPlugin end-to-end through a real agent + the engine.

Pins the opt-in/parity contract, the subagent-scoping fan-out (a sub-execution gets the
meta faculty from a per-item lobe override), and the next-turn flow-bias seam (a mid-turn
bias is persisted and routes the following turn).
"""

from __future__ import annotations

from agent_sdk import PreactAgent, probe
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


def test_plugin_adds_exactly_the_meta_context_lobe():
    agent = PreactAgent(client=scripted(lambda *a: "x"), plugins=[MetacognitionPlugin()])
    lobe_ids = {lb.id for lb in agent.engine.lobe_specs}
    bare_ids = {lb.id for lb in default_lobes()}
    assert lobe_ids - bare_ids == {"meta_context"}  # adds exactly one lobe, shifts nothing else


# ── subagent scoping via fan-out ─────────────────────────────────────────────────
class _FanModel:
    """meta_reflect: fan out 2 sub-tasks (one carrying its own meta faculty), then stop."""

    def __init__(self) -> None:
        self.fanned = False

    def __call__(self, stage, system, messages, tools):
        if stage == "meta_reflect" and not self.fanned:
            self.fanned = True
            return {
                "tools": [
                    {
                        "name": "meta_control",
                        "input": {
                            "action": "fan_out",
                            "items": [
                                {
                                    "label": "scoped",
                                    "input": "do scoped work",
                                    "lobes": ["meta_context"],
                                },
                                {"label": "plain", "input": "do plain work"},
                            ],
                        },
                    }
                ]
            }
        return "done"


async def test_fan_out_runs_one_scoped_subexecution_per_item():
    agent = PreactAgent(client=scripted(_FanModel()), plugins=[MetacognitionPlugin()])
    assert agent.inspect("rethink the approach here").path[0] == "meta"
    await probe(agent, "rethink the approach here", label="fan")

    fanout_calls = [c for c in agent.client.calls if c["stage"] == "meta_fanout"]
    assert len(fanout_calls) >= 2  # one scoped sub-execution per fanned item
    # the scoped item (lobes=["meta_context"]) borrows the meta faculty: its sub-prompt
    # carries the meta-context mirror block
    assert any("How you are thinking" in str(c["system"]) for c in fanout_calls)


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
