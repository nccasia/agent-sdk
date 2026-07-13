"""``OpenAIClient`` — the OpenAI Chat Completions API as an ``LlmCall``.

Adapts OpenAI's request/response shape to the engine's Anthropic-style contract:
Anthropic tool specs → OpenAI ``tools`` (function calling), the system string →
a leading ``system`` message, and the response → a :class:`Message` with
``TextBlock`` / ``ToolUseBlock`` content and a mapped ``stop_reason``. Requires
the optional ``openai`` extra (``pip install agent-sdk[openai]``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from agent_sdk.clients.base import BaseClient, _env
from agent_sdk.clients.messages import Message, ProviderUsage, TextBlock, ToolUseBlock
from agent_sdk.clients.openai_tools import openai_tools_payload, restore_tool_name

__all__ = ["OpenAIClient", "ProviderProtocolError"]

_FINISH_TO_STOP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
}

# Reasoning models behind OpenAI-compatible gateways (MiniMax, DeepSeek-R1, Qwen)
# emit chain-of-thought inside ``content`` as a ``<think>…</think>`` block instead
# of a separate field. Left in, it leaks into the user-facing answer. Strip it here.
# Truncation-tolerant: an unterminated block (``max_tokens`` cut mid-thought) is all
# reasoning and is matched to the end of the text (``\Z``). Real OpenAI never emits
# ``<think>``, so this is a no-op there.
_THINK_RE = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL)


def _strip_think(text: str) -> str:
    if "<think>" not in text:
        return text
    return _THINK_RE.sub("", text).strip()


class ProviderProtocolError(Exception):
    """Successful HTTP response that is not an OpenAI chat-completions response.

    Keep only an invariant code: provider diagnostics can contain credentials or
    request content and are unsafe to expose through worker logs or SSE events.
    """

    provider_failure_class = "protocol_incompatible"

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"provider protocol error: {code}")


_MISSING = object()


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _is_record(value: Any) -> bool:
    return isinstance(value, Mapping) or hasattr(value, "__dict__")


def _first_choice(response: Any) -> Any:
    if not _is_record(response):
        raise ProviderProtocolError("invalid_response_type")
    choices = _field(response, "choices", _MISSING)
    if choices is _MISSING:
        raise ProviderProtocolError("missing_choices")
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes, bytearray)):
        raise ProviderProtocolError("invalid_choices")
    if not choices:
        raise ProviderProtocolError("empty_choices")
    choice = choices[0]
    if not _is_record(choice):
        raise ProviderProtocolError("invalid_choice")
    message = _field(choice, "message")
    if not _is_record(message):
        raise ProviderProtocolError("missing_message")
    return choice


class OpenAIClient(BaseClient):
    provider = "openai"

    def __init__(
        self,
        model: str = "gpt-4.1",
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int = 2,
    ):
        super().__init__(model, api_key=api_key, base_url=base_url)
        self.max_retries = max_retries
        self._client: Any = None

    def _ensure(self) -> Any:
        if self._client is None:
            import openai

            self._client = openai.AsyncOpenAI(
                api_key=self.api_key or _env("OPENAI_API_KEY"),
                base_url=self.base_url or _env("OPENAI_BASE_URL"),
                max_retries=self.max_retries,
            )
        return self._client

    @staticmethod
    def _to_openai_tools(tools: list[dict] | None) -> tuple[list[dict] | None, dict[str, str]]:
        return openai_tools_payload(tools)

    @staticmethod
    def _to_openai_messages(system: str | list, messages: list[dict]) -> list[dict]:
        sys_text = system if isinstance(system, str) else _flatten_system(system)
        out: list[dict] = [{"role": "system", "content": sys_text}]
        for m in messages:
            out.append(_anthropic_msg_to_openai(m))
        return out

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
        client = self._ensure()
        kwargs: dict[str, Any] = {
            "model": self.model_for(stage),
            "messages": self._to_openai_messages(system, messages),
            "max_tokens": max_tokens,
            "temperature": 0.0 if temperature is None else temperature,
        }
        oai_tools, wire_to_canonical = self._to_openai_tools(tools)
        if oai_tools:
            kwargs["tools"] = oai_tools
        resp = await client.chat.completions.create(**kwargs)
        msg = self._adapt(resp, wire_to_canonical)
        if count_usage:
            self._record(msg.usage)
        return msg

    @staticmethod
    def _adapt(resp: Any, wire_to_canonical: dict[str, str] | None = None) -> Message:
        choice = _first_choice(resp)
        m = _field(choice, "message")
        blocks: list[Any] = []
        name_map = wire_to_canonical or {}
        content = _strip_think(_field(m, "content") or "")
        if content:
            blocks.append(TextBlock(text=content))
        for tc in _field(m, "tool_calls") or []:
            if not _is_record(tc):
                raise ProviderProtocolError("invalid_tool_call")
            function = _field(tc, "function")
            if not _is_record(function):
                raise ProviderProtocolError("invalid_tool_function")
            try:
                args = json.loads(_field(function, "arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            wire = _field(function, "name") or ""
            blocks.append(
                ToolUseBlock(
                    id=_field(tc, "id") or "",
                    name=restore_tool_name(wire, name_map),
                    input=args,
                )
            )
        if not blocks:
            blocks.append(TextBlock(text=""))
        stop = _FINISH_TO_STOP.get(_field(choice, "finish_reason") or "stop", "end_turn")
        u = _field(resp, "usage")
        usage = ProviderUsage(
            input_tokens=int(_field(u, "prompt_tokens") or 0) if u else 0,
            output_tokens=int(_field(u, "completion_tokens") or 0) if u else 0,
        )
        return Message(content=blocks, stop_reason=stop, usage=usage)


def _flatten_system(system: list) -> str:
    parts = []
    for block in system or []:
        if isinstance(block, dict):
            parts.append(str(block.get("text", "")))
        else:
            parts.append(str(getattr(block, "text", block)))
    return "\n".join(parts)


def _anthropic_msg_to_openai(m: dict) -> dict:
    """Convert one Anthropic-style message to OpenAI shape (text content only).

    Tool results inside a user message are flattened to text — sufficient for the
    single/agentic loops the engine drives.
    """
    role = m.get("role", "user")
    content = m.get("content", "")
    if isinstance(content, str):
        return {"role": role, "content": content}
    texts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                texts.append(str(block.get("text", "")))
            elif block.get("type") == "tool_result":
                texts.append(str(block.get("content", "")))
            elif block.get("type") == "tool_use":
                texts.append(f"[called {block.get('name')}({json.dumps(block.get('input', {}))})]")
        else:
            texts.append(str(block))
    return {"role": role, "content": "\n".join(texts)}
