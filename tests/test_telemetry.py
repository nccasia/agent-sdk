"""Phase 1 — context telemetry.

The engine must make the context cost *visible*: per-node attention tiers, a
per-stage input-token total, and a per-hop funnel-tail series — surfaced through
the trace → probe → viewer. All strictly additive (default behavior unchanged).
"""

from __future__ import annotations

from agent_sdk import Layer, Lobe, PreactAgent, flow, probe, stage, tool
from agent_sdk.clients import FakeClient
from agent_sdk.network.context_builder import ContextNode
from agent_sdk.result import Trace
from agent_sdk.viewer import to_viewer_record


class _Facts(Lobe):
    """A node-emitting lobe (so the attention pipeline produces tier telemetry)."""

    id = "facts"
    name = "Facts"
    description = "emit a couple of memory nodes"
    layer = Layer.COGNITION
    system_prompt = "Use the known facts."

    def activation(self, ctx: dict) -> float:
        return 1.0

    def build_context(self, _ctx):
        return [
            ContextNode(id="f1", kind="memory", text="alpha beta gamma delta", scope=None),
            ContextNode(id="f2", kind="memory", text="zeta eta theta iota", scope=None),
        ]


def _node_agent(script):
    @tool
    async def search(q: str) -> str:
        return "found the thing in a file " * 8  # a chunky observation to measure

    return PreactAgent(
        client=FakeClient(script),
        instructions="bot",
        lobes=[_Facts()],
        tools=[search],
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        stages=[stage("work", lobes=["facts"], loop="agentic", tools=["search"])],
        funnel=True,
    )


async def test_attention_tiers_captured():
    """A node-emitting lobe yields non-empty attention nodes/tiers in the trace."""
    agent = _node_agent(["the answer"])
    rec = await probe(agent, "what about alpha beta?", label="t")
    assert rec.attention, "attention rollup should be populated when nodes fire"
    assert rec.attention.get("nodes"), "per-node tier rows expected"
    # every node row carries the tier router's fields
    n = rec.attention["nodes"][0]
    assert {"id", "kind", "cds", "tier"} <= set(n)
    assert "tier_counts" in rec.attention


async def test_per_stage_metadata_present():
    """Each stage trace carries hops / input_tokens / funnel_obs_chars."""
    agent = _node_agent([{"tools": [{"name": "search", "input": {"q": "x"}}]}, "done"])
    rec = await probe(agent, "find alpha?", label="t")
    work = next(s for s in rec.stages if s["stage"] == "work")
    meta = work["metadata"]
    assert set(meta) >= {"hops", "input_tokens", "funnel_obs_chars"}
    assert meta["hops"] >= 1
    # one tool-using hop → one tail measurement recorded
    assert len(meta["funnel_obs_chars"]) >= 1
    assert meta["funnel_obs_chars"][0] > 0  # the chunky observation was measured


async def test_viewer_surfaces_real_attention_and_funnel():
    agent = _node_agent([{"tools": [{"name": "search", "input": {"q": "x"}}]}, "done"])
    rec = await probe(agent, "find alpha?", label="t")
    vr = to_viewer_record(rec)
    # the hard-coded empty attention is gone — real tiers flow through
    assert vr["trace"]["attention"].get("nodes")
    cf = vr["trace"]["context_funnel"]
    assert cf["stages"], "context_funnel panel data present"
    work = next(s for s in cf["stages"] if s["stage"] == "work")
    assert work["funnel_obs_chars"]  # the per-hop series reaches the viewer


def test_trace_schema_is_additive():
    """`Trace.to_json` keeps every legacy key and adds `attention` (no removals)."""
    legacy = {
        "trace_id",
        "path",
        "lobes",
        "flow_stages",
        "blackboard",
        "usage",
        "meta_actions",
        "llm_calls",
    }
    keys = set(Trace().to_json())
    assert legacy <= keys, f"a legacy trace key was dropped: {legacy - keys}"
    assert "attention" in keys


async def test_default_network_emits_no_tier_nodes():
    """No node-emitting lobe → zero tier nodes (the prompt is unchanged), though
    the per-stage token rollup is still recorded (additive telemetry value)."""
    agent = PreactAgent(client=FakeClient(["hi"]), instructions="bot")
    rec = await probe(agent, "hello?", label="t")
    assert rec.attention.get("nodes") == []  # no lobe emitted context nodes
    assert not any((rec.attention.get("tier_counts") or {}).values())  # nothing tiered
    # the rollup may still carry per-stage input_tokens — that's pure telemetry,
    # it does not change the composed prompt or message bytes.
