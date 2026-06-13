"""Typed streaming events + the ``AgentStream`` wrapper.

``agent.act(...)`` returns an :class:`AgentStream` — an async-iterable of typed,
pattern-matchable events that is **also awaitable** to the final result (à la the
Vercel AI SDK / Pydantic AI ``run_stream``). Events serialize 1:1 to JSON
(``ev.to_json()``) for SSE / pub-sub transport — the same wire shape the Mezon
worker publishes (``run_start`` / ``stage_start`` / ``tool_call`` / ``citation`` /
``final`` …); the JSON schema is in ``docs/porting.md`` §6.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, fields
from typing import Any

__all__ = [
    "AgentEvent",
    "RunStart",
    "PathResolved",
    "StageStart",
    "TextDelta",
    "ToolCall",
    "ToolResult",
    "CitationFound",
    "MetaAction",
    "StageEnd",
    "Final",
    "AgentStream",
]


def _jsonify(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "to_json"):
        return value.to_json()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    return str(value)


class _EventBase:
    """Mixin: ``type`` tag + uniform ``to_json``. Payload fields come first so
    positional matching (``case TextDelta(text)``) binds the payload."""

    type: str = "event"

    def to_json(self) -> dict:
        out: dict[str, Any] = {"type": self.type}
        for f in fields(self):  # type: ignore[arg-type]
            out[f.name] = _jsonify(getattr(self, f.name))
        return out


@dataclass
class RunStart(_EventBase):
    trace_id: str = ""
    ts: float = 0.0
    type: str = "run_start"


@dataclass
class PathResolved(_EventBase):
    path: str = ""
    score: float = 0.0
    trace_id: str = ""
    ts: float = 0.0
    type: str = "path_resolved"


@dataclass
class StageStart(_EventBase):
    flow: str = ""
    stage: str = ""
    trace_id: str = ""
    ts: float = 0.0
    type: str = "stage_start"


@dataclass
class TextDelta(_EventBase):
    text: str = ""
    trace_id: str = ""
    ts: float = 0.0
    type: str = "text_delta"


@dataclass
class ToolCall(_EventBase):
    id: str = ""
    name: str = ""
    input: dict | None = None
    trace_id: str = ""
    ts: float = 0.0
    type: str = "tool_call"


@dataclass
class ToolResult(_EventBase):
    id: str = ""
    name: str = ""
    output: str = ""
    trace_id: str = ""
    ts: float = 0.0
    type: str = "tool_result"


@dataclass
class CitationFound(_EventBase):
    citation: Any = None
    trace_id: str = ""
    ts: float = 0.0
    type: str = "citation"


@dataclass
class MetaAction(_EventBase):
    action: str = ""
    reason: str = ""
    trace_id: str = ""
    ts: float = 0.0
    type: str = "meta_action"


@dataclass
class StageEnd(_EventBase):
    flow: str = ""
    stage: str = ""
    usage: Any = None
    trace_id: str = ""
    ts: float = 0.0
    type: str = "stage_end"


@dataclass
class Final(_EventBase):
    result: Any = None
    trace_id: str = ""
    ts: float = 0.0
    type: str = "final"


AgentEvent = (
    RunStart
    | PathResolved
    | StageStart
    | TextDelta
    | ToolCall
    | ToolResult
    | CitationFound
    | MetaAction
    | StageEnd
    | Final
)


def stamp(event: Any, trace_id: str) -> Any:
    """Fill ``trace_id`` + ``ts`` on an event (the emitter's single touch-point)."""
    event.trace_id = trace_id
    event.ts = time.time()
    return event


class AgentStream:
    """Async-iterable of events that is also awaitable to the final result.

    Consume it one of three ways (single consumption, like Pydantic AI):

        async for ev in stream: ...          # typed events
        async for chunk in stream.text_stream: ...   # just the text deltas
        result = await stream                # drain + return the AgentResult
    """

    def __init__(self, source: Any):
        self._source = source.__aiter__() if hasattr(source, "__aiter__") else source
        self._result: Any = None
        self._done = False

    async def __aiter__(self):
        async for ev in self._source:
            if isinstance(ev, Final):
                self._result = ev.result
            yield ev
        self._done = True

    @property
    def text_stream(self):
        async def gen():
            async for ev in self:
                if isinstance(ev, TextDelta):
                    yield ev.text

        return gen()

    async def result(self) -> Any:
        if not self._done and self._result is None:
            async for _ev in self:
                pass
        return self._result

    def __await__(self):
        return self.result().__await__()
