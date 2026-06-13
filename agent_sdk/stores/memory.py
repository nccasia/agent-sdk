"""Memory stores — the pluggable durable-memory backend.

``MemoryStore`` is the protocol; ``MemoryStoreInMemory`` is the default;
``MemoryStoreRedis`` (a hash per scope) is the production adapter. ``search`` is a
deterministic token-overlap match (no embeddings) so it works with zero infra; an
``Embed`` seam can be layered on top for semantic recall.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol, runtime_checkable

from agent_sdk.memory import MemoryItem

__all__ = ["MemoryStore", "MemoryStoreInMemory", "MemoryStoreRedis"]


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[\W_]+", str(text).lower()) if t}


def _overlap_search(items: dict[str, Any], query: str, k: int) -> list[MemoryItem]:
    q = _tokens(query)
    scored: list[tuple[float, str, Any]] = []
    for key, value in items.items():
        hay = _tokens(f"{key} {value}")
        score = len(q & hay) / (len(q) or 1)
        if score > 0:
            scored.append((score, key, value))
    scored.sort(key=lambda x: -x[0])
    return [
        MemoryItem(scope="", key=key, value=value, score=round(s, 4))
        for s, key, value in scored[:k]
    ]


@runtime_checkable
class MemoryStore(Protocol):
    async def read(self, scope: str, key: str) -> Any: ...
    async def write(self, scope: str, key: str, value: Any) -> None: ...
    async def search(self, scope: str, query: str, k: int = 5) -> list[MemoryItem]: ...
    async def forget(self, scope: str, key: str) -> bool: ...


class MemoryStoreInMemory:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    async def read(self, scope: str, key: str) -> Any:
        return self._data.get(scope, {}).get(key)

    async def write(self, scope: str, key: str, value: Any) -> None:
        self._data.setdefault(scope, {})[key] = value

    async def search(self, scope: str, query: str, k: int = 5) -> list[MemoryItem]:
        items = _overlap_search(self._data.get(scope, {}), query, k)
        for it in items:
            it.scope = scope
        return items

    async def forget(self, scope: str, key: str) -> bool:
        return self._data.get(scope, {}).pop(key, _MISSING) is not _MISSING


class MemoryStoreRedis:
    def __init__(
        self, url: str | None = None, *, client: Any | None = None, prefix: str = "memory:"
    ):
        self._url = url
        self._client = client
        self._prefix = prefix

    def _conn(self) -> Any:
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.from_url(self._url or "redis://localhost:6379")
        return self._client

    def _hkey(self, scope: str) -> str:
        return f"{self._prefix}{scope}"

    @staticmethod
    def _dec(raw: Any) -> Any:
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    async def read(self, scope: str, key: str) -> Any:
        return self._dec(await self._conn().hget(self._hkey(scope), key))

    async def write(self, scope: str, key: str, value: Any) -> None:
        await self._conn().hset(self._hkey(scope), key, json.dumps(value))

    async def search(self, scope: str, query: str, k: int = 5) -> list[MemoryItem]:
        raw = await self._conn().hgetall(self._hkey(scope))
        items = {
            (kk.decode() if isinstance(kk, bytes) else kk): self._dec(vv)
            for kk, vv in (raw or {}).items()
        }
        out = _overlap_search(items, query, k)
        for it in out:
            it.scope = scope
        return out

    async def forget(self, scope: str, key: str) -> bool:
        return bool(await self._conn().hdel(self._hkey(scope), key))


_MISSING = object()
