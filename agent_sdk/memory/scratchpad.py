"""Turn-scoped scratchpad — the reasoning process's flash memory (RAM).

A small structured key→value buffer that lives for ONE turn. Stages and the
model (via the ``scratchpad.*`` tools) write the turn's working state here —
sub-questions, goals, decisions, detected language, intermediate findings — so
it can be **offloaded out of the system prompt** (smaller per-step prompts, less
context burned) and **recalled by any downstream step** without losing it.

Three things it is NOT:
- ``TurnContext.lobe_outputs`` — an untyped, untraced engine handoff dict.
- the memo ``Blackboard`` — a node pool with a compression invariant (memo
  shaped objects only; raw chunks rejected).
- persistent ``memory`` / ``context.set`` — durable, CROSS-turn storage.

The scratchpad is the within-turn, ephemeral, traced sibling of those: it is
reset every turn and snapshotted into the trace for inspection.
"""

from __future__ import annotations

import json
from typing import Any

# Size caps — the whole point is to SAVE context, so keep the buffer bounded.
_CAP_KEYS = 64  # distinct keys per turn
_CAP_VALUE_CHARS = 8000  # serialized chars per value (capped, not dropped)
_CAP_LIST = 64  # items appended to a single list key
_MISSING = object()  # sentinel for delete()


def _over(value: Any) -> bool:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str)) > _CAP_VALUE_CHARS
    except Exception:
        return True


def _cap_value(value: Any) -> Any:
    """Keep values JSON-serializable and bounded — **preserving container type**.

    Oversized strings are head/tail-elided. An oversized **list stays a list** (each item
    capped; tail items dropped with an ``{"_elided": n}`` marker) and an oversized **dict
    stays a dict** — never collapsed into a sentinel object. This matters because consumers
    read these back via ``as_list`` + ``isinstance(item, dict)`` (e.g. fan-out
    ``todos_results``): collapsing a list into a dict silently read as *zero* items and
    sank wide fan-outs. Nothing is lost-silent; the shape survives."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        value = str(value)
        encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded) <= _CAP_VALUE_CHARS:
        return json.loads(encoded)
    if isinstance(value, str):
        keep = _CAP_VALUE_CHARS
        return f"{value[: keep * 3 // 4]}\n…[+{len(value) - keep} chars elided]…\n{value[-keep // 4 :]}"
    if isinstance(value, list):
        capped = [_cap_value(v) for v in value]
        while len(capped) > 1 and _over(capped):
            capped.pop()
        dropped = len(value) - len(capped)
        if dropped:
            capped.append({"_elided": dropped})  # marker; consumers skip non-data items
        return capped
    if isinstance(value, dict):
        capped = {k: _cap_value(v) for k, v in value.items()}
        order = list(capped.keys())
        while order and _over(capped):
            capped.pop(order.pop())
        capped["_truncated"] = True
        return capped
    return {"_truncated": True, "preview": encoded[:_CAP_VALUE_CHARS]}


class Scratchpad:
    """Turn-scoped key→value flash memory. JSON-serializable values, bounded."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        key = str(key)
        if key not in self._data and len(self._data) >= _CAP_KEYS:
            return  # at capacity — refuse new keys (existing keys still update)
        self._data[key] = _cap_value(value)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(str(key), default)

    def delete(self, key: str) -> bool:
        """Drop a key (turn-scope ``memory forget``). Returns whether it existed."""
        return self._data.pop(str(key), _MISSING) is not _MISSING

    def append(self, key: str, value: Any) -> None:
        """Append to a list-valued key (creating it as a list)."""
        key = str(key)
        cur = self._data.get(key)
        if not isinstance(cur, list):
            cur = [] if cur is None else [cur]
        if len(cur) < _CAP_LIST:
            cur.append(_cap_value(value))
        self.set(key, cur)

    def keys(self) -> list[str]:
        return list(self._data.keys())

    def __contains__(self, key: str) -> bool:
        return str(key) in self._data

    def __bool__(self) -> bool:
        return bool(self._data)

    def as_list(self, key: str) -> list[Any]:
        """Read a key as a list — the fan-out work-list accessor. A scalar
        becomes a one-item list; missing → []."""
        v = self._data.get(str(key))
        if v is None:
            return []
        return list(v) if isinstance(v, list) else [v]

    def snapshot(self) -> dict[str, Any]:
        """A JSON-safe deep copy for the trace (inspector renders this)."""
        try:
            return json.loads(json.dumps(self._data, ensure_ascii=False, default=str))
        except Exception:
            return {k: str(v) for k, v in self._data.items()}
