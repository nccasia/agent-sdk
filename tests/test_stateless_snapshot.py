"""Stateless serving API — snapshot/restore, whole-state save, pooled multi-session isolation.

Unit-level locks on the public surface statelessbench exercises end-to-end. Deterministic
(FakeClient); universal memory is driven by auto-establish from the input (no LLM).
"""

from __future__ import annotations

from agent_sdk import PreactAgent
from agent_sdk.clients import FakeClient
from agent_sdk.serve import AgentWorker, InProcessEventSink, InProcessQueue, Job
from agent_sdk.session import SNAPSHOT_VERSION, Session, SessionState
from agent_sdk.stores.session import SessionStoreInMemory


def _agent() -> PreactAgent:
    return PreactAgent(client=FakeClient(["Noted."] * 8), instructions="You are helpful.")


def _mem_text(snapshot: dict) -> str:
    long = ((snapshot or {}).get("memory") or {}).get("long") or []
    return " ".join(str(e.get("body", "")) for e in long).lower()


async def test_run_snapshot_roundtrips_memory_across_fresh_agents():
    a1 = _agent()
    _r1, snap = await a1.run_snapshot("Remember:\n- My name is Alice")
    assert snap["v"] == SNAPSHOT_VERSION
    assert "alice" in _mem_text(snap)
    assert len(snap["history"]) == 2

    # A different process/replica continues from the JSON alone.
    a2 = _agent()
    _r2, snap2 = await a2.run_snapshot("What's my name?", snapshot=snap)
    assert "alice" in _mem_text(snap2)  # memory survived the hop
    assert len(snap2["history"]) == 4  # prior turns carried


async def test_run_snapshot_empty_start():
    a = _agent()
    result, snap = await a.run_snapshot("hello")
    assert result.status == "answered"
    assert snap["history"][0]["content"] == "hello"


async def test_session_store_saves_whole_state_including_memory():
    store = SessionStoreInMemory()
    await _agent().query("Remember:\n- The window is Thursday", session=Session("s1", store))
    state = await store.load("s1")
    assert "thursday" in _mem_text(state.to_json())  # memory persisted, not just turns
    assert len(state.history) == 2


async def test_sessionless_query_unchanged():
    # No session → no restore/capture; the agent keeps accumulating in-process (legacy behavior).
    a = _agent()
    r = await a.query("hi")
    assert r.status == "answered"


async def test_session_state_snapshot_is_versioned_and_tolerant():
    full = SessionState(summary="s", skills_in_use=["k"], memory={"seq": 1, "long": []}).to_json()
    assert full["v"] == SNAPSHOT_VERSION and "memory" in full
    # unknown future key ignored; missing keys default
    st = SessionState.from_json({**full, "_future": 1})
    assert st.skills_in_use == ["k"]
    assert SessionState.from_json({"summary": "legacy"}).memory == {}


async def test_agent_worker_pool_isolates_concurrent_sessions():
    store = SessionStoreInMemory()
    worker = AgentWorker(agent_factory=_agent, queue=InProcessQueue(),
                         sink=InProcessEventSink(), store=store, concurrency=2)
    names = ["alice", "bob", "carol", "dave"]  # pool(2) < sessions(4) → agents reused
    for n in names:
        await worker.queue.enqueue(Job(input=f"Remember:\n- My name is {n}", session_id=f"s-{n}"))
    await worker.serve(max_jobs=len(names))

    for n in names:
        facts = _mem_text((await store.load(f"s-{n}")).to_json())
        assert n in facts
        assert not [o for o in names if o != n and o in facts]  # no cross-session bleed


async def test_worker_resumes_session_by_id():
    store = SessionStoreInMemory()
    worker = AgentWorker(agent_factory=_agent, queue=InProcessQueue(),
                         sink=InProcessEventSink(), store=store)
    await worker.queue.enqueue(Job(input="Remember:\n- My name is Amy", session_id="s"))
    await worker.serve(max_jobs=1)
    await worker.queue.enqueue(Job(input="Remember:\n- I work at Acme", session_id="s"))
    await worker.serve(max_jobs=1)

    snap = (await store.load("s")).to_json()
    assert "amy" in _mem_text(snap) and "acme" in _mem_text(snap)
    assert len(snap["history"]) == 4  # resumed by id, both turns in the one JSON


def test_agent_worker_requires_agent_or_factory():
    import pytest

    with pytest.raises(ValueError):
        AgentWorker(queue=InProcessQueue(), sink=InProcessEventSink())


async def test_pool_resets_memory_across_sessionless_jobs():
    # A pooled agent reused across SESSIONLESS jobs must not carry the prior job's memory —
    # otherwise one bot/tenant's working memory could bleed into the next. Hold the one pooled
    # agent (concurrency=1 forces reuse) and inspect its store directly.
    agent = _agent()
    worker = AgentWorker(agent_factory=lambda: agent, queue=InProcessQueue(),
                         sink=InProcessEventSink(), concurrency=1)

    await worker.queue.enqueue(Job(input="Remember:\n- My name is Alice"))  # no session
    await worker.serve(max_jobs=1)
    assert "alice" in _mem_text({"memory": agent._memory_store.to_json()})  # stored on the agent

    await worker.queue.enqueue(Job(input="hello again"))  # no session, reuses the SAME agent
    await worker.serve(max_jobs=1)
    # the worker reset the agent on checkout → Alice is gone, no bleed into the next job
    assert "alice" not in _mem_text({"memory": agent._memory_store.to_json()})
