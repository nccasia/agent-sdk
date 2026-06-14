"""Native-efficiency defaults — a plain PreactAgent is memory- and funnel-equipped out of the box.

Locks in the redesign agentbench drove: the default agent natively memorizes (recall/note tools + a
memory directive in the prompt) and bounds context (funnel on). The bare, memoryless behavior is still
reachable via the opt-out flags (rollback). These are deterministic (no provider).
"""

from __future__ import annotations

from agent_sdk import PreactAgent
from agent_sdk.agent import MEMORY_DIRECTIVE
from agent_sdk.clients import FakeClient


def _tool_names(agent):
    rt = agent.engine.tools
    return {s["name"] for s in rt.get_tool_specs()} if rt is not None else set()


def test_default_agent_is_natively_efficient():
    agent = PreactAgent(client=FakeClient(["ok"]), instructions="You are helpful.")
    # memory is wired by default
    assert agent._memory_store is not None
    assert {"recall", "note"} <= _tool_names(agent)
    # the funnel bounds context by default
    assert agent.engine.funnel is True
    # the memory directive is in the engine's prompt — but NOT in the user's instructions / spec
    assert agent.engine.system_addendum == MEMORY_DIRECTIVE
    assert agent.instructions == "You are helpful."
    assert agent.spec().instructions == "You are helpful."


def test_opt_out_restores_bare_behavior():
    agent = PreactAgent(
        client=FakeClient(["ok"]), instructions="bare", funnel=False, universal_memory=False
    )
    assert agent._memory_store is None
    assert not ({"recall", "note"} & _tool_names(agent))
    assert agent.engine.funnel is False
    assert agent.engine.system_addendum == ""


def test_establish_extracts_fact_shaped_statements():
    from agent_sdk.memory.establish import salient_facts

    text = (
        "New messages in #ops:\n"
        "- The zephyr deadline is 2026-07-15.\n"
        "- @lan owns the orion project.\n"
        "lol nice\n"
        "The rollout is scheduled for Friday 14:00."
    )
    facts = salient_facts(text)
    assert any("2026-07-15" in f for f in facts)
    assert any("@lan" in f for f in facts)
    assert any("Friday 14:00" in f for f in facts)
    assert not any(f.strip() == "lol nice" for f in facts)  # chatter dropped


async def test_default_agent_auto_establishes_facts():
    """A plain agent reliably memorizes the facts in a turn — no note call needed (native offload)."""
    from agent_sdk import probe

    agent = PreactAgent(client=FakeClient(["Got it."]), instructions="assistant")
    await probe(agent, "Note: the cutover window is Saturday 02:00 UTC and @user042 owns rollback.")
    hits = {h.handle for h in agent._memory_store.recall(query="cutover rollback")}
    bodies = " ".join(agent._memory_store.read(h) for h in hits)
    assert "Saturday 02:00" in bodies and "@user042" in bodies  # established without a tool call


async def test_default_funnel_bounds_a_long_tool_loop():
    """A plain agent's tool loop stays bounded (the default funnel budget compacts the tail)."""
    from agent_sdk import flow, probe, stage, tool

    @tool
    async def fetch(record_id: int) -> str:
        return "lorem ipsum dolor sit amet detail " * 30  # ~1020 chars, like the bench

    script = [{"tools": [{"name": "fetch", "input": {"record_id": i}}]} for i in range(18)] + [
        "done"
    ]
    agent = PreactAgent(
        client=FakeClient(script),
        instructions="loop",
        tools=[fetch],
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        stages=[stage("work", lobes=["synthesize"], loop="agentic", tools=["fetch"], hops=24)],
    )
    rec = await probe(agent, "fetch records 1..18")
    series = [c for s in rec.stages for c in (s.get("metadata") or {}).get("funnel_obs_chars", [])]
    peak = max(series or [0])
    n = sum(1 for c in rec.tool_calls if c.get("name") == "fetch")
    assert n >= 16, f"only {n} fetch calls"
    assert 0 < peak < 9000, f"funnel tail not bounded: peak={peak} over {n} calls"


async def test_default_agent_can_offload_and_recall():
    """End-to-end on a FakeClient: the default agent's note→recall round-trips through its store."""
    from agent_sdk import probe

    agent = PreactAgent(
        client=FakeClient(
            [
                {
                    "tools": [
                        {
                            "name": "note",
                            "input": {
                                "content": "the launch is Friday",
                                "kind": "fact",
                                "scope": "conversation",
                                "key": "launch",
                            },
                        }
                    ]
                },
                "Noted.",
            ]
        ),
        instructions="assistant",
    )
    await probe(agent, "remember the launch is Friday")
    # the fact is durably in the store, recallable later
    hits = agent._memory_store.recall(query="launch")
    assert hits and "Friday" in agent._memory_store.read(hits[0].handle)
