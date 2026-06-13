"""``FakeClient`` — a deterministic, scriptable LLM client for tests + dev.

Drive the engine end-to-end without a network. Script a sequence of turns; each
script item is one of:

- ``str`` → a final text answer (``stop_reason="end_turn"``).
- ``dict`` with ``"tools"`` → tool_use blocks (``stop_reason="tool_use"``)::

      {"tools": [{"name": "search", "input": {"query": "x"}}]}

  optionally with ``"text"`` for accompanying thinking text.
- ``dict`` with ``"text"`` → a final text answer.
- ``callable(stage, system, messages, tools) -> (str | dict | Message)`` → dynamic.

When the script is exhausted, ``default`` is returned (a plain text answer). The
client records token usage (a length/4 estimate) so usage accounting is testable.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from agent_sdk.clients.base import BaseClient
from agent_sdk.clients.messages import Message, ProviderUsage, TextBlock, ToolUseBlock

__all__ = ["FakeClient"]


def _est(text: str) -> int:
    return max(1, len(text) // 4)


class FakeClient(BaseClient):
    provider = "fake"

    def __init__(
        self,
        script: Sequence[Any] | None = None,
        *,
        default: str = "OK.",
        model: str = "fake-model",
    ):
        super().__init__(model)
        self._script: list[Any] = list(script or [])
        self._i = 0
        self._tool_seq = 0  # monotonic tool_use id counter (real providers are unique)
        self.default = default
        self.calls: list[dict] = []  # recorded call args, for assertions

    def _next_item(self, stage: str, system: Any, messages: list[dict], tools: Any) -> Any:
        if self._i < len(self._script):
            item = self._script[self._i]
            self._i += 1
            if callable(item) and not isinstance(item, (str, dict)):
                return item(stage, system, messages, tools)
            return item
        return self.default

    def _to_message(self, item: Any) -> Message:
        if isinstance(item, Message):
            return item
        if isinstance(item, str):
            content = [TextBlock(text=item)]
            usage = ProviderUsage(input_tokens=4, output_tokens=_est(item))
            return Message(content=content, stop_reason="end_turn", usage=usage)
        if isinstance(item, dict):
            blocks: list[Any] = []
            text = item.get("text")
            if text:
                blocks.append(TextBlock(text=str(text)))
            tool_calls = item.get("tools") or []
            for tc in tool_calls:
                # A real provider emits a globally-unique tool_use id per call;
                # use a monotonic counter so multi-hop funnel logic (which pairs +
                # tiers observations by id) behaves as it would in production.
                self._tool_seq += 1
                blocks.append(
                    ToolUseBlock(
                        id=tc.get("id", f"call_{self._tool_seq}"),
                        name=tc["name"],
                        input=tc.get("input", {}),
                    )
                )
            stop = "tool_use" if tool_calls else item.get("stop_reason", "end_turn")
            usage = ProviderUsage(input_tokens=4, output_tokens=_est(str(text or "")))
            return Message(content=blocks or [TextBlock(text="")], stop_reason=stop, usage=usage)
        raise TypeError(f"unsupported FakeClient script item: {item!r}")

    async def __call__(
        self,
        *,
        stage: str,
        system: str | list,
        messages: list[dict],
        max_tokens: int,
        temperature: float | None = None,
        tools: list[dict] | None = None,
        count_usage: bool = True,
    ) -> Message:
        self.calls.append(
            {
                "stage": stage,
                "system": system,
                "messages": messages,
                "tools": tools,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        item = self._next_item(stage, system, messages, tools)
        msg = self._to_message(item)
        if count_usage:
            self._record(msg.usage)
        return msg


def scripted(handler: Callable[..., Any]) -> FakeClient:
    """A FakeClient driven entirely by a handler ``(stage, system, messages, tools)``."""
    client = FakeClient([])
    client._script = [handler] * 10_000  # effectively unbounded
    return client
