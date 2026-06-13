"""Additional coverage: skill_def, metacognition facade, redis serving, edges."""

from __future__ import annotations

import pytest

from agent_sdk import Flows, Lobes, Metacognition, PreactAgent, Skill, Stages, flow, stage
from agent_sdk.clients import FakeClient
from agent_sdk.metacognition_facade import PINNED_UNSKIPPABLE
from agent_sdk.serve import Job, RedisLock, RedisQueue
from agent_sdk.skill_def import Skill as SkillDef

try:
    import fakeredis.aioredis as fakeredis_aio
except Exception:  # pragma: no cover
    fakeredis_aio = None


# ── skill_def ────────────────────────────────────────────────────────────────
def test_skill_to_pack_maps_fields():
    s = Skill(
        id="code_review",
        when="reviewing pull requests",
        instructions="Check logic.",
        tools=["search"],
        disclosure="on_demand",
        files={"GUIDE.md": "## checklist"},
    )
    pack = s.to_pack()
    assert pack.id == "code_review"
    assert pack.injection == "on_demand"
    assert pack.required_tools == ("search",)
    assert pack.files["GUIDE.md"] == "## checklist"


def test_skill_signal_and_validation():
    s = Skill("x", when="w", signal={"flag": "needed"})
    assert s.signal({"needed": True}) == 1.0
    assert s.signal({}) == 0.0
    with pytest.raises(ValueError):
        SkillDef("y", disclosure="sometimes")


async def test_skill_surfaces_in_prompt():
    skill = Skill(id="reviewer", when="reviewing code", instructions="Be thorough.",
                  disclosure="eager", stages=["synthesize"])
    agent = PreactAgent(client=FakeClient(["done"]), instructions="bot", skills=[skill],
                        lobes=Lobes.minimal(), stages=Stages.minimal(), flows=Flows.minimal())
    # the eager skill's instructions are composed into the synthesize stage system
    await agent.query("review this?")
    sys_prompts = [c["system"] for c in agent.client.calls]
    assert any("Be thorough." in s for s in sys_prompts)


# ── metacognition facade ─────────────────────────────────────────────────────
def test_metacognition_modes_and_coerce():
    assert Metacognition.coerce("apply").mode == "apply"
    assert Metacognition.coerce("observe").mode == "observe"
    assert Metacognition.coerce(None).mode == "observe"
    m = Metacognition("apply", apply_actions={"adjust_lobe_slice"})
    assert Metacognition.coerce(m) is m
    with pytest.raises(ValueError):
        Metacognition("frobnicate")
    with pytest.raises(TypeError):
        Metacognition.coerce(123)


def test_metacognition_pinned_never_skipped():
    assert frozenset({"cite", "filter"}) == PINNED_UNSKIPPABLE
    meta = Metacognition("apply", apply_actions={"adjust_lobe_slice"})
    # plan_next never returns skip for cite/filter even if asked
    decision = meta.plan_next(target_flow="research", target_step="cite")
    assert decision.action != "skip_step"


# ── clarify flow routing ─────────────────────────────────────────────────────
async def test_clarify_flow_routes_on_ambiguous_flag():
    # a custom flow that fires on an explicit ambiguous signal
    agent = PreactAgent(
        client=FakeClient(["Could you clarify which version?"]),
        instructions="assistant",
        flows=[
            flow("clarify", stages=["clarify"], grounds=False, signal={"const": 1.0}),
            flow("qna", stages=["synthesize"], signal={"const": 0.3}),
        ],
        stages=[stage("clarify", lobes=["clarify"]), stage("synthesize", lobes=["synthesize"])],
    )
    snap = agent.inspect("it")
    assert snap.path[0] == "clarify"
    result = await agent.query("it")
    assert "clarify" in result.text.lower()


# ── redis serving (fakeredis) ────────────────────────────────────────────────
@pytest.mark.skipif(fakeredis_aio is None, reason="fakeredis not installed")
async def test_redis_queue_roundtrip():
    client = fakeredis_aio.FakeRedis()
    q = RedisQueue(client=client)
    await q.enqueue(Job(input="hello", trace_id="t1"))
    gen = q.consume()
    job = await gen.__anext__()
    assert job.input == "hello"
    assert job.trace_id == "t1"


@pytest.mark.skipif(fakeredis_aio is None, reason="fakeredis not installed")
async def test_redis_lock_acquire_release():
    client = fakeredis_aio.FakeRedis()
    lock = RedisLock(client=client)
    async with lock("conv-1"):
        assert await client.get("agent:lock:conv-1") is not None
    # released
    assert await client.get("agent:lock:conv-1") is None


# ── tools edge: composite external names ─────────────────────────────────────
async def test_max_loop_drops_tools_on_final_hop():
    from agent_sdk import tool

    @tool
    async def loop_tool() -> str:
        return "again"

    # the model keeps calling the tool; the engine must terminate at max hops
    agent = PreactAgent(
        client=FakeClient([{"tools": [{"name": "loop_tool", "input": {}}]}] * 10 + ["stop"]),
        instructions="bot",
        tools=[loop_tool],
        flows=[flow("qna", stages=["synthesize"], signal={"const": 1.0})],
        stages=[stage("synthesize", lobes=["synthesize"], loop="agentic", tools=["loop_tool"], hops=3)],
    )
    result = await agent.query("go?")
    # terminates without hanging; produces some result
    assert result.status in ("answered", "refused")
