"""Session stores — the pluggable conversation-state backend.

``SessionStore`` is the protocol; ``SessionStoreInMemory`` (zero infra) is the
default; ``SessionStoreRedis`` (JSON under ``session:<id>``) and
``SessionStoreSQL`` (a single sqlite blob per id, stdlib only) are the
production adapters.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any, Protocol, runtime_checkable

from agent_sdk.session import SessionState, Summarizer, Turn

__all__ = [
    "SessionStore",
    "SessionStoreInMemory",
    "SessionStoreRedis",
    "SessionStoreSQL",
]


@runtime_checkable
class SessionStore(Protocol):
    async def load(self, id: str) -> SessionState: ...
    async def append(self, id: str, turn: Turn) -> None: ...
    async def compact(self, id: str, summarizer: Summarizer, *, keep_last: int = 6) -> None: ...


async def _do_compact(state: SessionState, summarizer: Summarizer, keep_last: int) -> None:
    if len(state.history) <= keep_last:
        return
    old, recent = state.history[:-keep_last], state.history[-keep_last:]
    new_summary = await summarizer(old)
    state.summary = (state.summary + "\n" + new_summary).strip() if state.summary else new_summary
    state.history = recent


class SessionStoreInMemory:
    """Process-local store — the zero-infra default."""

    def __init__(self) -> None:
        self._data: dict[str, SessionState] = {}

    async def load(self, id: str) -> SessionState:
        return self._data.setdefault(id, SessionState())

    async def append(self, id: str, turn: Turn) -> None:
        self._data.setdefault(id, SessionState()).history.append(turn)

    async def compact(self, id: str, summarizer: Summarizer, *, keep_last: int = 6) -> None:
        state = self._data.setdefault(id, SessionState())
        await _do_compact(state, summarizer, keep_last)


class SessionStoreRedis:
    """Redis-backed store (JSON blob under ``session:<id>``)."""

    def __init__(
        self, url: str | None = None, *, client: Any | None = None, prefix: str = "session:"
    ):
        self._url = url
        self._client = client
        self._prefix = prefix

    def _conn(self) -> Any:
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.from_url(self._url or "redis://localhost:6379")
        return self._client

    def _key(self, id: str) -> str:
        return f"{self._prefix}{id}"

    async def load(self, id: str) -> SessionState:
        raw = await self._conn().get(self._key(id))
        if not raw:
            return SessionState()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return SessionState.from_json(json.loads(raw))

    async def _save(self, id: str, state: SessionState) -> None:
        await self._conn().set(self._key(id), json.dumps(state.to_json()))

    async def append(self, id: str, turn: Turn) -> None:
        state = await self.load(id)
        state.history.append(turn)
        await self._save(id, state)

    async def compact(self, id: str, summarizer: Summarizer, *, keep_last: int = 6) -> None:
        state = await self.load(id)
        await _do_compact(state, summarizer, keep_last)
        await self._save(id, state)


class SessionStoreSQL:
    """SQLite-backed store (stdlib only) — one JSON blob per id.

    Synchronous sqlite3 runs in a thread executor so it never blocks the loop.
    ``dsn`` is a file path or ``":memory:"``.
    """

    def __init__(self, dsn: str = ":memory:"):
        self._conn = sqlite3.connect(dsn, check_same_thread=False)
        self._conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, state TEXT)")
        self._conn.commit()
        self._lock = asyncio.Lock()

    async def _exec(self, fn: Any) -> Any:
        async with self._lock:
            return await asyncio.get_event_loop().run_in_executor(None, fn)

    async def load(self, id: str) -> SessionState:
        def _q() -> SessionState:
            row = self._conn.execute("SELECT state FROM sessions WHERE id=?", (id,)).fetchone()
            return SessionState.from_json(json.loads(row[0])) if row else SessionState()

        return await self._exec(_q)

    async def _save(self, id: str, state: SessionState) -> None:
        payload = json.dumps(state.to_json())

        def _w() -> None:
            self._conn.execute(
                "INSERT INTO sessions(id, state) VALUES(?, ?) "
                "ON CONFLICT(id) DO UPDATE SET state=excluded.state",
                (id, payload),
            )
            self._conn.commit()

        await self._exec(_w)

    async def append(self, id: str, turn: Turn) -> None:
        state = await self.load(id)
        state.history.append(turn)
        await self._save(id, state)

    async def compact(self, id: str, summarizer: Summarizer, *, keep_last: int = 6) -> None:
        state = await self.load(id)
        await _do_compact(state, summarizer, keep_last)
        await self._save(id, state)
