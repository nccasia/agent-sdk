"""``Memory`` — durable, scoped agent memory + the auto-wired ``memory`` tool.

Scopes: ``turn`` (the always-on :class:`Scratchpad`) · ``conversation`` ·
``channel`` · ``user`` · ``bot``. Attaching ``Memory`` to an agent auto-wires the
``memory`` tool (remember / recall / forget within the allowed scopes). Durable
profiles and rules are just ``user`` / ``bot``-scoped memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["MemoryItem", "Memory", "MemoryToolRuntime"]

DEFAULT_SCOPES = ("conversation", "channel", "user", "bot")


@dataclass
class MemoryItem:
    scope: str
    key: str
    value: Any
    score: float = 1.0

    def to_json(self) -> dict:
        return {"scope": self.scope, "key": self.key, "value": self.value, "score": self.score}


class Memory:
    """Durable agent memory over a pluggable ``MemoryStore``."""

    def __init__(self, store: Any | None = None, scopes: list[str] | tuple[str, ...] | None = None):
        if store is None:
            from agent_sdk.stores.memory import MemoryStoreInMemory

            store = MemoryStoreInMemory()
        self.store = store
        self.scopes = tuple(scopes) if scopes else DEFAULT_SCOPES

    def _check(self, scope: str) -> None:
        if scope not in self.scopes:
            raise ValueError(f"scope {scope!r} not in allowed scopes {self.scopes}")

    async def read(self, scope: str, key: str) -> Any:
        self._check(scope)
        return await self.store.read(scope, key)

    async def write(self, scope: str, key: str, value: Any) -> None:
        self._check(scope)
        await self.store.write(scope, key, value)

    async def search(self, scope: str, query: str, k: int = 5) -> list[MemoryItem]:
        self._check(scope)
        return await self.store.search(scope, query, k)

    async def forget(self, scope: str, key: str) -> bool:
        self._check(scope)
        return await self.store.forget(scope, key)

    def tool_runtime(self) -> MemoryToolRuntime:
        return MemoryToolRuntime(self)


class MemoryToolRuntime:
    """A ``ToolRuntime`` exposing one ``memory`` tool (remember/recall/forget)."""

    def __init__(self, memory: Memory):
        self.memory = memory
        self.updates: list[dict] = []  # structured {action, scope, key} this turn

    def get_tool_specs(self) -> list[dict]:
        return [
            {
                "name": "memory",
                "description": (
                    "Durable memory across the conversation. Use action=remember to save a "
                    "fact, recall to look one up (by key or free-text query), forget to delete."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["remember", "recall", "forget"]},
                        "scope": {"type": "string", "enum": list(self.memory.scopes)},
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                        "query": {"type": "string"},
                    },
                    "required": ["action", "scope"],
                },
            }
        ]

    async def call_tool(
        self,
        name: str,
        inp: dict,
        retrieved_chunks: list[dict] | None = None,
        already_read: set[str] | None = None,
    ) -> str:
        if name != "memory":
            return f"Error: unknown tool '{name}'."
        action = inp.get("action")
        scope = inp.get("scope", "conversation")
        key = inp.get("key", "")
        try:
            if action == "remember":
                await self.memory.write(scope, key, inp.get("value", ""))
                self.updates.append({"action": "remember", "scope": scope, "key": key})
                return f"Remembered {key!r} in {scope}."
            if action == "recall":
                if inp.get("query"):
                    items = await self.memory.search(scope, inp["query"])
                    return "\n".join(f"- {i.key}: {i.value}" for i in items) or "(nothing found)"
                val = await self.memory.read(scope, key)
                return f"{key}: {val}" if val is not None else "(not set)"
            if action == "forget":
                ok = await self.memory.forget(scope, key)
                if ok:
                    self.updates.append({"action": "forget", "scope": scope, "key": key})
                return "Forgotten." if ok else "(nothing to forget)"
        except ValueError as exc:
            return f"Error: {exc}"
        return f"Error: unknown action {action!r}."
