"""End-to-end PreactAgent turns driven by a deterministic FakeClient."""

from __future__ import annotations

import json

from agent_sdk import (
    ActivationSnapshot,
    AgentResult,
    Final,
    Flows,
    Lobes,
    Memory,
    PreactAgent,
    Session,
    Stages,
    ToolCall,
    tool,
)
from agent_sdk.clients import FakeClient
from agent_sdk.stores import MemoryStoreInMemory, SessionStoreInMemory

_MIN = dict(lobes=Lobes.minimal(), stages=Stages.minimal(), flows=Flows.minimal())


def _agent(script, **kw):
    return PreactAgent(client=FakeClient(script), instructions="You are helpful.", **kw)


async def test_quickstart_one_shot():
    agent = _agent(["v2 added streaming and a new spec."])
    result = await agent.query("What changed in v2?")
    assert isinstance(result, AgentResult)
    assert result.status == "answered"
    assert "streaming" in result.text
    assert result.usage.output_tokens > 0
    assert result.trace.path["name"] in ("qna", "research", "clarify", "emergent")


async def test_streaming_events_and_text_stream():
    agent = _agent(["Hello world answer."])
    seen_types = []
    async for ev in agent.act("hi?"):
        seen_types.append(type(ev).__name__)
    assert "RunStart" in seen_types
    assert "PathResolved" in seen_types
    assert "Final" in seen_types


async def test_stream_awaitable_to_result():
    agent = _agent(["awaited answer"])
    result = await agent.act("question?")
    assert result.text == "awaited answer"


async def test_text_stream_only():
    agent = _agent(["just the text"])
    chunks = [c async for c in agent.act("q?").text_stream]
    assert "just the text" in "".join(chunks)


async def test_research_flow_with_tools():
    @tool
    async def search(query: str) -> str:
        return json.dumps(
            {
                "results": ["finding A"],
                "citations": [{"chunk_id": "c1", "source_ref": "doc#1", "supporting_span": [0, 5]}],
            }
        )

    # Force the research flow ("compare" trips its lexical signal).
    agent = PreactAgent(
        client=FakeClient(
            [
                "Plan: compare A and B.",  # plan stage
                {"tools": [{"name": "search", "input": {"query": "A vs B"}}]},  # research hop 1
                "Gathered evidence.",  # research hop 2 (answer)
                "A beats B on streaming.",  # synthesize
            ]
        ),
        instructions="Researcher.",
        tools=[search],
        **_MIN,
    )
    tool_calls = []
    result = None
    async for ev in agent.act("compare A and B in detail please thanks"):
        if isinstance(ev, ToolCall):
            tool_calls.append(ev.name)
        if isinstance(ev, Final):
            result = ev.result
    assert result is not None
    assert tool_calls == ["search"]
    assert result.text == "A beats B on streaming."
    assert result.citations and result.citations[0].chunk_id == "c1"


async def test_inspect_is_no_llm():
    fake = FakeClient(["should not be called"])
    agent = PreactAgent(client=fake, instructions="x")
    snap = agent.inspect("compare A and B in detail please now")
    assert isinstance(snap, ActivationSnapshot)
    assert snap.path[0] == "research"
    assert any("investigate" in s for s in snap.flow)  # production: flow-qualified ids
    assert fake.calls == []  # inspect never touches the model


async def test_session_persists_history():
    store = SessionStoreInMemory()
    session = Session("conv-7", store)
    agent = PreactAgent(client=FakeClient(["a1", "a2"]), session=session)
    await agent.query("first?")
    await agent.query("second?")
    state = await store.load("conv-7")
    contents = [t.content for t in state.history]
    assert contents == ["first?", "a1", "second?", "a2"]


async def test_memory_tool_wired_and_updates_recorded():
    @tool
    async def noop() -> str:
        return "ok"

    mem = Memory(MemoryStoreInMemory(), scopes=["user"])
    agent = PreactAgent(
        client=FakeClient(
            [
                {
                    "tools": [
                        {
                            "name": "memory",
                            "input": {
                                "action": "remember",
                                "scope": "user",
                                "key": "name",
                                "value": "Minh",
                            },
                        }
                    ]
                },
                "Saved your name.",
            ]
        ),
        instructions="assistant",
        tools=[noop],
        memory=mem,
        flows=[__import__("agent_sdk").flow("qna", stages=["synthesize"], signal={"const": 1.0})],
        stages=[
            __import__("agent_sdk").stage(
                "synthesize",
                lobes=["synthesize", "cite", "filter"],
                loop="agentic",
                tools=["memory", "noop"],
            )
        ],
    )
    result = await agent.query("remember my name is Minh")
    assert await mem.read("user", "name") == "Minh"
    assert any(u.action == "remember" for u in result.memory_updates)


async def test_require_citations_refuses_without_sources():
    agent = PreactAgent(
        client=FakeClient(["ungrounded claim"]),
        instructions="grounded bot",
        require_citations=True,
    )
    result = await agent.query("what is the deploy day?")
    assert result.status == "refused"
    assert result.refusal.reason == "no_citations"


async def test_share_history_threads_stages():
    # With share_history, a later stage's messages include earlier stages' work.
    captured = {}

    def handler(stage, system, messages, tools):
        captured[stage] = list(messages)
        return f"{stage} done"

    agent = PreactAgent(
        client=FakeClient([handler] * 20),
        instructions="bot",
        flows=[__import__("agent_sdk").flow("multi", stages=["a", "b"], signal={"const": 1.0})],
        stages=[
            __import__("agent_sdk").stage("a", lobes=["synthesize"]),
            __import__("agent_sdk").stage("b", lobes=["synthesize"]),
        ],
        share_history=True,
    )
    await agent.query("do it")
    # stage b sees more messages than stage a (a's output + the transition)
    assert len(captured["b"]) > len(captured["a"])
    assert "assistant" in [m["role"] for m in captured["b"]]


async def test_isolated_history_is_default():
    captured = {}

    def handler(stage, system, messages, tools):
        captured.setdefault(stage, []).append(len(messages))
        return f"{stage} done"

    agent = PreactAgent(
        client=FakeClient([handler] * 20),
        instructions="bot",
        flows=[__import__("agent_sdk").flow("multi", stages=["a", "b"], signal={"const": 1.0})],
        stages=[
            __import__("agent_sdk").stage("a", lobes=["synthesize"]),
            __import__("agent_sdk").stage("b", lobes=["synthesize"]),
        ],
    )
    await agent.query("do it")
    # default: each stage sees the same base messages (isolated)
    assert captured["a"] == captured["b"]


async def test_with_immutable_copy():
    base = _agent(["x"])
    other = base.with_(instructions="different")
    assert other.instructions == "different"
    assert base.instructions == "You are helpful."


async def test_submit_and_events():
    agent = _agent(["queued answer"])
    job = await agent.submit("question?")
    evs = [type(e).__name__ async for e in agent.events(job)]
    assert "Final" in evs


async def test_last_trace_and_optimizations():
    agent = _agent(["answer text"])
    await agent.query("q?")
    assert agent.last_trace is not None
    # answered stage produced text → no disable suggestion for it
    opts = agent.suggest_optimizations()
    assert isinstance(opts, list)
