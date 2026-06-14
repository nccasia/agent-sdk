"""``AgentContext`` — the single shared-context handle every component holds.

One object, reached three ways (a lobe's ``TurnContext.context`` field, a tool's
``current_context()`` contextvar, a skill's runtime arg), exposing ALL of an
agent's state through one scoped interface — from the turn in front of it out to
what the bot knows.

It is a thin **router**, not a store and not a cache: scoped reads/writes pass
straight through to the live backends, so there is exactly one source of truth.

    scope=turn                       → Scratchpad (RAM, dropped at turn end)
    scope=conversation|channel|user|bot → Memory   (durable, scoped key→value)

plus ambient read-only turn facts (``identity``/``channel``/``session``/
``evidence``). See ``docs/concepts/07-shared-context.md``.

This module is **leaf-safe** (stdlib + ``agent_sdk`` only) and **opt-in** — the
default network is byte-identical until an integrator constructs and threads it.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agent_sdk.memory.durable import Memory, MemoryItem
from agent_sdk.memory.scratchpad import Scratchpad

__all__ = ["Scope", "Evidence", "AgentContext", "current_context", "bind_context"]


class Scope(StrEnum):
    """The durability ladder — narrowest (RAM) to widest (durable, shared).

    ``turn`` routes to the :class:`Scratchpad` (working scratch, dropped at turn
    end); the rest route to durable :class:`Memory`. A component picks a
    *durability*, never a backend.
    """

    TURN = "turn"
    CONVERSATION = "conversation"
    CHANNEL = "channel"
    USER = "user"
    BOT = "bot"

    @classmethod
    def coerce(cls, scope: Scope | str) -> Scope:
        return scope if isinstance(scope, cls) else cls(str(scope))


@dataclass
class Evidence:
    """The turn's shared evidence channel — retrieved chunks + a dedupe set.

    A view over the SAME two objects the engine threads into every ``call_tool``
    (``TurnContext.retrieved_chunks`` / ``already_read``), so a grounding lobe and
    a KB tool see one accumulating pool. Raw chunks live HERE, never in the
    scoped bag — the compression invariant.
    """

    retrieved_chunks: list[dict] = field(default_factory=list)
    already_read: set[str] = field(default_factory=set)

    def add(self, chunk: Mapping[str, Any]) -> bool:
        """Append a chunk, deduped by ``chunk_id``. Returns whether it was new."""
        cid = str(chunk.get("chunk_id") or chunk.get("id") or "")
        if cid and cid in self.already_read:
            return False
        if cid:
            self.already_read.add(cid)
        self.retrieved_chunks.append(dict(chunk))
        return True

    def __len__(self) -> int:
        return len(self.retrieved_chunks)


class AgentContext:
    """The shared-context handle: one scoped interface over all agent state.

    Construct directly, or with :meth:`from_turn` off a live ``TurnContext``.
    ``memory`` (a durable :class:`Memory`) is required only for the durable
    scopes; an agent with turn-scope-only state needs no durable backend.
    """

    def __init__(
        self,
        *,
        query: str = "",
        scratchpad: Scratchpad | None = None,
        memory: Memory | None = None,
        session: Any = None,
        identity: Mapping[str, Any] | None = None,
        channel: Mapping[str, Any] | None = None,
        evidence: Evidence | None = None,
        stage_id: str | None = None,
        path: str | None = None,
        active_lobes: frozenset[str] | tuple[str, ...] = (),
    ) -> None:
        self.query = query
        self._scratchpad = scratchpad if scratchpad is not None else Scratchpad()
        self._memory = memory
        self._session = session
        self._identity: Mapping[str, Any] = identity or {}
        self._channel: Mapping[str, Any] = channel or {}
        self._evidence = evidence if evidence is not None else Evidence()
        self.stage_id = stage_id
        self.path = path
        self.active_lobes = frozenset(active_lobes)

    # ── construction off a turn ─────────────────────────────────────────────
    @classmethod
    def from_turn(cls, turn: Any, *, memory: Memory | None = None) -> AgentContext:
        """Build a context that wraps a live ``TurnContext``'s state.

        Duck-typed (reads attributes off ``turn``) so this module need not import
        the ``TurnContext`` contract — keeping the dependency one-directional and
        the facade usable with any turn-shaped object. The evidence view shares
        the turn's SAME ``retrieved_chunks`` / ``already_read`` objects.
        """
        # Share the turn's SAME evidence objects — note ``or`` would discard an
        # empty (falsy) list/set, so default explicitly on None only.
        chunks = getattr(turn, "retrieved_chunks", None)
        seen = getattr(turn, "already_read", None)
        ev = Evidence(
            retrieved_chunks=chunks if chunks is not None else [],
            already_read=seen if seen is not None else set(),
        )
        return cls(
            query=getattr(turn, "query", "") or "",
            scratchpad=getattr(turn, "scratchpad", None),
            memory=memory,
            session=getattr(turn, "session_memory", None),
            identity=getattr(turn, "identity", None),
            channel=getattr(turn, "channel", None),
            evidence=ev,
            stage_id=getattr(turn, "stage_id", None),
            path=getattr(turn, "active_path", None),
            active_lobes=getattr(turn, "active_lobes", ()) or (),
        )

    # ── scoped state (the unification) ──────────────────────────────────────
    async def get(
        self, key: str, *, scope: Scope | str = Scope.TURN, default: Any = None
    ) -> Any:
        """Read one value at ``scope``. ``turn`` → scratchpad; else → durable memory."""
        sc = Scope.coerce(scope)
        if sc is Scope.TURN:
            return self._scratchpad.get(key, default)
        val = await self._mem().read(sc.value, key)
        return default if val is None else val

    async def set(self, key: str, value: Any, *, scope: Scope | str = Scope.TURN) -> None:
        """Write one value at ``scope``. The same write the ``scratchpad``/``memory`` tools make."""
        sc = Scope.coerce(scope)
        if sc is Scope.TURN:
            self._scratchpad.set(key, value)
            return
        await self._mem().write(sc.value, key, value)

    async def delete(self, key: str, *, scope: Scope | str = Scope.TURN) -> bool:
        """Drop one value at ``scope``. Returns whether it existed."""
        sc = Scope.coerce(scope)
        if sc is Scope.TURN:
            return self._scratchpad.delete(key)
        return await self._mem().forget(sc.value, key)

    async def search(
        self, query: str, *, scope: Scope | str = Scope.CONVERSATION, k: int = 5
    ) -> list[MemoryItem]:
        """Free-text find at ``scope``. Turn scope does a cheap substring scan of the
        scratchpad; durable scopes delegate to the backend's relevance search."""
        sc = Scope.coerce(scope)
        if sc is Scope.TURN:
            return self._scratchpad_search(query, k)
        return await self._mem().search(sc.value, query, k)

    # ── ambient, read-only (the turn's shape) ───────────────────────────────
    @property
    def identity(self) -> Mapping[str, Any]:
        """Who: principal / user / tenant. Host-provided, never model-forged."""
        return self._identity

    @property
    def channel(self) -> Mapping[str, Any]:
        """Where: room / workspace context."""
        return self._channel

    @property
    def session(self) -> Any:
        """The conversation: history, summary, facts (a ``SessionState`` or None)."""
        return self._session

    @property
    def evidence(self) -> Evidence:
        """The turn's shared evidence channel (retrieved chunks + dedupe set)."""
        return self._evidence

    @property
    def scratchpad(self) -> Scratchpad:
        """The turn's RAM, for direct sync use inside a hot loop."""
        return self._scratchpad

    @property
    def has_durable(self) -> bool:
        """Whether a durable backend is attached (the non-turn scopes are usable)."""
        return self._memory is not None

    # ── internals ───────────────────────────────────────────────────────────
    def _mem(self) -> Memory:
        if self._memory is None:
            raise RuntimeError(
                "AgentContext has no durable Memory — only scope=turn is available. "
                "Construct with memory=Memory(...) to use conversation/channel/user/bot scopes."
            )
        return self._memory

    def _scratchpad_search(self, query: str, k: int) -> list[MemoryItem]:
        q = (query or "").lower()
        out: list[MemoryItem] = []
        pad_keys = self._scratchpad.keys()
        for key in pad_keys:
            val = self._scratchpad.get(key)
            if not q or q in key.lower() or q in str(val).lower():
                out.append(MemoryItem(scope=Scope.TURN.value, key=key, value=val))
            if len(out) >= k:
                break
        return out


# ── the seam: one contextvar every tool/skill reaches ───────────────────────
_CONTEXT: contextvars.ContextVar[AgentContext | None] = contextvars.ContextVar(
    "agent_sdk_context", default=None
)


def current_context() -> AgentContext | None:
    """The active :class:`AgentContext` (or None) — the seam a tool or skill uses
    to reach shared state without an explicit argument. Set for the duration of a
    turn via :func:`bind_context` (mirrors the engine's ``current_turn()``)."""
    return _CONTEXT.get()


@contextlib.contextmanager
def bind_context(ctx: AgentContext | None) -> Iterator[AgentContext | None]:
    """Bind ``ctx`` as the active shared context for the enclosed block, restoring
    the previous binding on exit (re-entrant / nesting-safe)."""
    token = _CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _CONTEXT.reset(token)
