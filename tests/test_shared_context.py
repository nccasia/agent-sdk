"""Shared Context — the one-handle-every-scope facade.

Gates: scoped round-trip (turn → bot), the durable/turn routing split, the
ambient read-only views, the ``from_turn`` wrap (shared evidence objects), and
the ``current_context()`` / ``bind_context()`` seam. Leaf isolation is covered
by ``tests/test_sdk_isolation.py`` (the whole package).
"""

from __future__ import annotations

import pytest

from agent_sdk import (
    AgentContext,
    Evidence,
    Memory,
    Scope,
    bind_context,
    current_context,
)
from agent_sdk.contracts.turn import TurnContext


def _ctx_with_memory() -> AgentContext:
    return AgentContext(query="hi", memory=Memory())  # in-memory durable backend


# ── scoped round-trip ────────────────────────────────────────────────────────


async def test_turn_scope_roundtrips_through_scratchpad():
    ctx = AgentContext()
    await ctx.set("plan", ["a", "b"], scope=Scope.TURN)
    assert await ctx.get("plan", scope=Scope.TURN) == ["a", "b"]
    # the SAME scratchpad a tool would reach via .scratchpad
    assert ctx.scratchpad.get("plan") == ["a", "b"]
    assert await ctx.delete("plan", scope=Scope.TURN) is True
    assert await ctx.get("plan", scope=Scope.TURN, default="gone") == "gone"


async def test_turn_scope_is_the_default():
    ctx = AgentContext()
    await ctx.set("lang", "vi")
    assert await ctx.get("lang") == "vi"
    assert ctx.scratchpad.get("lang") == "vi"


@pytest.mark.parametrize(
    "scope", [Scope.CONVERSATION, Scope.CHANNEL, Scope.USER, Scope.BOT]
)
async def test_durable_scopes_roundtrip_through_memory(scope):
    ctx = _ctx_with_memory()
    await ctx.set("ui_pref", "dark", scope=scope)
    assert await ctx.get("ui_pref", scope=scope) == "dark"
    assert await ctx.get("missing", scope=scope, default="d") == "d"
    assert await ctx.delete("ui_pref", scope=scope) is True
    assert await ctx.get("ui_pref", scope=scope) is None


async def test_scopes_are_isolated():
    ctx = _ctx_with_memory()
    await ctx.set("k", "turn-val", scope=Scope.TURN)
    await ctx.set("k", "user-val", scope=Scope.USER)
    await ctx.set("k", "bot-val", scope=Scope.BOT)
    assert await ctx.get("k", scope=Scope.TURN) == "turn-val"
    assert await ctx.get("k", scope=Scope.USER) == "user-val"
    assert await ctx.get("k", scope=Scope.BOT) == "bot-val"


async def test_scope_accepts_plain_strings():
    ctx = _ctx_with_memory()
    await ctx.set("k", "v", scope="user")
    assert await ctx.get("k", scope="user") == "v"


# ── search ───────────────────────────────────────────────────────────────────


async def test_search_turn_scope_scans_scratchpad():
    ctx = AgentContext()
    await ctx.set("deploy_window", "Thursday 14:00")
    await ctx.set("owner", "lan")
    hits = await ctx.search("deploy", scope=Scope.TURN)
    assert any(h.key == "deploy_window" for h in hits)


async def test_search_durable_scope_delegates_to_backend():
    ctx = _ctx_with_memory()
    await ctx.set("pref", "user prefers dark mode", scope=Scope.USER)
    hits = await ctx.search("dark mode", scope=Scope.USER)
    assert hits and hits[0].key == "pref"


# ── durable backend required for durable scopes ──────────────────────────────


async def test_durable_scope_without_memory_raises():
    ctx = AgentContext()  # no memory
    assert ctx.has_durable is False
    with pytest.raises(RuntimeError, match="no durable Memory"):
        await ctx.get("k", scope=Scope.USER)


async def test_scope_allowlist_is_enforced_by_memory():
    # Memory restricts writable scopes; the context surfaces that error.
    ctx = AgentContext(memory=Memory(scopes=("conversation",)))
    with pytest.raises(ValueError, match="not in allowed scopes"):
        await ctx.set("k", "v", scope=Scope.BOT)


# ── ambient read-only views ──────────────────────────────────────────────────


async def test_ambient_views_are_exposed():
    ctx = AgentContext(
        query="who am i",
        identity={"user_id": "u1", "tenant_id": "t1"},
        channel={"channel_id": "c1"},
        session="SESSION_STATE",
    )
    assert ctx.query == "who am i"
    assert ctx.identity["user_id"] == "u1"
    assert ctx.channel["channel_id"] == "c1"
    assert ctx.session == "SESSION_STATE"


async def test_evidence_dedupes_by_chunk_id():
    ev = Evidence()
    assert ev.add({"chunk_id": "a", "text": "x"}) is True
    assert ev.add({"chunk_id": "a", "text": "x again"}) is False  # dupe
    assert ev.add({"chunk_id": "b", "text": "y"}) is True
    assert len(ev) == 2
    assert ev.already_read == {"a", "b"}


# ── from_turn: wrap a live TurnContext, share its evidence objects ───────────


async def test_from_turn_wraps_turn_state():
    chunks: list[dict] = []
    seen: set[str] = set()
    turn = TurnContext(
        query="q",
        stage_id="synthesize",
        active_path="qna",
        identity={"user_id": "u9"},
        channel={"channel_id": "c9"},
        session_memory="S",
        retrieved_chunks=chunks,
        already_read=seen,
    )
    ctx = AgentContext.from_turn(turn)  # turn.scratchpad is None → facade makes one
    assert ctx.query == "q"
    assert ctx.stage_id == "synthesize"
    assert ctx.path == "qna"
    assert ctx.identity["user_id"] == "u9"
    assert ctx.session == "S"
    # the evidence view shares the SAME underlying objects the engine threads
    # into call_tool, so a chunk added via the context is visible on the turn.
    ctx.evidence.add({"chunk_id": "z", "text": "t"})
    assert chunks == [{"chunk_id": "z", "text": "t"}]
    assert seen == {"z"}


# ── the seam ─────────────────────────────────────────────────────────────────


async def test_current_context_seam_binds_and_restores():
    assert current_context() is None
    ctx = AgentContext(query="bound")
    with bind_context(ctx):
        assert current_context() is ctx
        # a tool, reaching shared state without an explicit arg:
        await current_context().set("note", "from a tool")
        assert ctx.scratchpad.get("note") == "from a tool"
    assert current_context() is None  # restored


async def test_bind_context_nests():
    outer = AgentContext(query="outer")
    inner = AgentContext(query="inner")
    with bind_context(outer):
        assert current_context() is outer
        with bind_context(inner):
            assert current_context() is inner
        assert current_context() is outer  # inner restored
