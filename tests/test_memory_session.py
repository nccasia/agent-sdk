"""Session + Memory + their stores."""

from __future__ import annotations

import pytest

from agent_sdk.memory import Memory, MemoryToolRuntime
from agent_sdk.session import Session, Turn
from agent_sdk.stores import (
    MemoryStoreInMemory,
    MemoryStoreRedis,
    SessionStoreInMemory,
    SessionStoreRedis,
    SessionStoreSQL,
)

try:
    import fakeredis.aioredis as fakeredis_aio
except Exception:  # pragma: no cover
    fakeredis_aio = None


async def _summarizer(turns):
    return f"[{len(turns)} earlier turns]"


# ── Session ──────────────────────────────────────────────────────────────────
async def test_session_append_and_load_inmemory():
    s = Session("conv-1", SessionStoreInMemory())
    await s.append(Turn("user", "hello"))
    await s.append(Turn("assistant", "hi"))
    state = await s.load()
    assert [t.content for t in state.history] == ["hello", "hi"]
    msgs = state.messages()
    assert msgs[0]["role"] == "user"


async def test_session_compact_rolls_into_summary():
    s = Session("c", SessionStoreInMemory())
    for i in range(10):
        await s.append(Turn("user", f"m{i}"))
    await s.compact(_summarizer, keep_last=3)
    state = await s.load()
    assert len(state.history) == 3
    assert "earlier turns" in state.summary


async def test_session_default_store_is_inmemory():
    s = Session("x")
    assert isinstance(s.store, SessionStoreInMemory)


async def test_session_sql_store_roundtrip():
    store = SessionStoreSQL(":memory:")
    await store.append("c1", Turn("user", "persisted"))
    state = await store.load("c1")
    assert state.history[0].content == "persisted"


@pytest.mark.skipif(fakeredis_aio is None, reason="fakeredis not installed")
async def test_session_redis_store_roundtrip():
    client = fakeredis_aio.FakeRedis()
    store = SessionStoreRedis(client=client)
    await store.append("c2", Turn("assistant", "from redis"))
    state = await store.load("c2")
    assert state.history[0].content == "from redis"


# ── Memory ───────────────────────────────────────────────────────────────────
async def test_memory_write_read_search_forget():
    m = Memory(MemoryStoreInMemory(), scopes=["user", "bot"])
    await m.write("user", "deploy_day", "Friday")
    assert await m.read("user", "deploy_day") == "Friday"
    items = await m.search("user", "deploy")
    assert items and items[0].key == "deploy_day"
    assert await m.forget("user", "deploy_day") is True
    assert await m.read("user", "deploy_day") is None


async def test_memory_scope_enforcement():
    m = Memory(MemoryStoreInMemory(), scopes=["user"])
    with pytest.raises(ValueError):
        await m.write("bot", "k", "v")


async def test_memory_tool_runtime_remember_recall_forget():
    m = Memory(MemoryStoreInMemory(), scopes=["conversation"])
    rt = MemoryToolRuntime(m)
    specs = rt.get_tool_specs()
    assert specs[0]["name"] == "memory"

    out = await rt.call_tool(
        "memory",
        {"action": "remember", "scope": "conversation", "key": "name", "value": "Minh"},
        [],
        set(),
    )
    assert "Remembered" in out
    assert rt.updates == [{"action": "remember", "scope": "conversation", "key": "name"}]

    recall = await rt.call_tool(
        "memory", {"action": "recall", "scope": "conversation", "key": "name"}, [], set()
    )
    assert "Minh" in recall

    forget = await rt.call_tool(
        "memory", {"action": "forget", "scope": "conversation", "key": "name"}, [], set()
    )
    assert "Forgotten" in forget


@pytest.mark.skipif(fakeredis_aio is None, reason="fakeredis not installed")
async def test_memory_redis_store():
    client = fakeredis_aio.FakeRedis()
    m = Memory(MemoryStoreRedis(client=client), scopes=["bot"])
    await m.write("bot", "tone", "friendly")
    assert await m.read("bot", "tone") == "friendly"
    items = await m.search("bot", "tone")
    assert items[0].key == "tone"
