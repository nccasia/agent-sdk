"""Serializable spec round-trip."""

from __future__ import annotations

import json

from agent_sdk import Flows, Lobes, PreactAgent, Stages
from agent_sdk.clients import FakeClient
from agent_sdk.spec import PreactSpec, agent_from_spec


def _agent():
    # The minimal network has declarative-signal flows that round-trip through
    # the spec; the production default's Python recognizers do not serialize.
    return PreactAgent(
        client=FakeClient(["x"]), instructions="You are helpful.",
        lobes=Lobes.minimal(), stages=Stages.minimal(), flows=Flows.minimal(),
    )


def test_build_spec_captures_network():
    spec = _agent().spec()
    assert isinstance(spec, PreactSpec)
    assert spec.instructions == "You are helpful."
    assert {lb["id"] for lb in spec.lobes} >= {"classify", "synthesize", "cite", "filter"}
    assert {f["id"] for f in spec.flows} >= {"research", "clarify", "qna"}
    assert "cite" in spec.pinned_lobes


def test_spec_json_round_trips():
    spec = _agent().spec()
    blob = spec.to_json_str()
    restored = PreactSpec.from_json(json.loads(blob))
    assert restored.to_json() == spec.to_json()


def test_from_spec_rebuilds_routing():
    agent = _agent()
    spec = agent.spec()
    rebuilt = agent_from_spec(spec, client=FakeClient(["y"]))
    # routing (flow recognition) round-trips: a "compare" query → research
    snap = rebuilt.inspect("compare A and B in great detail right now thanks")
    assert snap.path[0] == "research"
    # a short direct question → qna
    snap2 = rebuilt.inspect("hi?")
    assert snap2.path[0] == "qna"


async def test_from_spec_agent_runs():
    spec = _agent().spec()
    rebuilt = agent_from_spec(spec, client=FakeClient(["rebuilt answer"]))
    result = await rebuilt.query("a question?")
    assert result.text == "rebuilt answer"
    # the synthesize lobe (always-on) round-trips as activated
    activated = {lb["id"] for lb in result.trace.lobes if lb["activated"]}
    assert "synthesize" in activated
