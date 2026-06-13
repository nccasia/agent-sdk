"""Memory — turn-scope scratchpad (RAM) + durable scoped agent memory.

``Scratchpad`` is the always-on ``turn`` scope (offload → recall within a turn).
``Memory`` is the durable cross-conversation store (``conversation`` / ``channel``
/ ``user`` / ``bot`` scopes) that auto-wires the ``memory`` tool.
"""

from __future__ import annotations

from agent_sdk.memory.durable import (
    DEFAULT_SCOPES,
    Memory,
    MemoryItem,
    MemoryToolRuntime,
)
from agent_sdk.memory.scratchpad import Scratchpad

__all__ = [
    "Scratchpad",
    "Memory",
    "MemoryItem",
    "MemoryToolRuntime",
    "DEFAULT_SCOPES",
]
