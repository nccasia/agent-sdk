"""Pluggable persistence backends for Session and Memory.

In-memory defaults need zero infra; Redis / SQL adapters swap in at scale. The
same agent runs in a unit test, in-process, or behind a Redis pool by swapping
the store — the seam is the only thing that changes.
"""

from __future__ import annotations

from agent_sdk.stores.memory import (
    MemoryStore,
    MemoryStoreInMemory,
    MemoryStoreRedis,
)
from agent_sdk.stores.session import (
    SessionStore,
    SessionStoreInMemory,
    SessionStoreRedis,
    SessionStoreSQL,
)

__all__ = [
    "SessionStore",
    "SessionStoreInMemory",
    "SessionStoreRedis",
    "SessionStoreSQL",
    "MemoryStore",
    "MemoryStoreInMemory",
    "MemoryStoreRedis",
]
