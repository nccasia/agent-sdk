"""Phase 3 — the always-on tiered ``## Memory`` index.

A turn-start prefetch hook loads the durable store into
``TurnContext.memory_items`` so the ``memory_recall`` lobe renders the relevant
facts as context nodes — most recalls then cost zero tool calls. Backward
compatible: an empty store contributes nothing.
"""

from __future__ import annotations

from agent_sdk import PreactAgent, probe
from agent_sdk.clients import FakeClient
from agent_sdk.memory.durable import Memory
from agent_sdk.memory.prefetch import memory_prefetch_hook
from agent_sdk.plugins.base import AgentSetup
from agent_sdk.stores.memory import MemoryStoreInMemory


async def _seeded_memory():
    m = Memory(MemoryStoreInMemory(), scopes=["bot", "user", "channel", "conversation"])
    await m.write("channel", "deploy_freeze", "deploy freeze until Monday June 15")
    await m.write("user", "review_language", "write code reviews in Vietnamese")
    return m


# ── the prefetch hook in isolation ────────────────────────────────────────────
async def test_hook_returns_scope_ordered_items():
    m = await _seeded_memory()
    hook = memory_prefetch_hook(m, k=5)
    out = await hook("when is the deploy freeze", None)
    items = out["memory_items"]
    assert items, "matching facts should be prefetched"
    assert any(i["key"] == "deploy_freeze" and "Monday" in str(i["value"]) for i in items)
    # broad → specific: any bot/user entries precede channel/conversation ones
    order = {"bot": 0, "user": 1, "channel": 2, "conversation": 3}
    ranks = [order.get(i["scope"], 9) for i in items]
    assert ranks == sorted(ranks)


async def test_hook_over_budget_value_degrades_to_hint():
    m = Memory(MemoryStoreInMemory(), scopes=["bot"])
    await m.write("bot", "manual", "deploy " + "x" * 5000)  # huge value, matches query
    hook = memory_prefetch_hook(m, value_budget_chars=100)
    out = await hook("deploy", None)
    item = next(i for i in out["memory_items"] if i["key"] == "manual")
    assert item["value"] == ""  # cleared — over budget
    assert "recall to read" in item["description"]  # surfaced as a hint instead


async def test_hook_empty_store_is_noop():
    m = Memory(MemoryStoreInMemory(), scopes=["bot", "user"])
    out = await memory_prefetch_hook(m)("anything at all", None)
    assert out == {"memory_items": []}


def test_agentsetup_registers_prefetch_hook():
    setup = AgentSetup()

    async def h(_q, _s):
        return {"memory_items": []}

    setup.add_prefetch_hook(h)
    assert setup.prefetch_hooks == [h]


# ── end-to-end: zero-tool-call recall from the injected index ─────────────────
async def test_memory_index_injected_zero_tool_calls():
    """A relevant fact reaches the prompt via the index — no recall tool call."""
    m = await _seeded_memory()
    agent = PreactAgent(
        client=FakeClient(["The deploy freeze is until Monday June 15."]),
        instructions="bot",
        memory=m,  # built-in prefetch hook auto-registers
    )
    rec = await probe(agent, "when is the deploy freeze?", label="t")
    # the fact rode in as a context node (kind=memory) — no tool call needed
    mem_nodes = [n for n in (rec.attention.get("nodes") or []) if n.get("kind") == "memory"]
    assert mem_nodes, "the ## Memory index should contribute a memory context node"
    assert not any(c["name"] == "memory" for c in rec.tool_calls)  # zero recall calls


async def test_no_memory_no_prefetch_noop():
    """An agent without a Memory registers no prefetch hook (byte-identical)."""
    agent = PreactAgent(client=FakeClient(["hi"]), instructions="bot")
    assert agent.engine._prefetch_hooks == []
    rec = await probe(agent, "hello?", label="t")
    assert not [n for n in (rec.attention.get("nodes") or []) if n.get("kind") == "memory"]
