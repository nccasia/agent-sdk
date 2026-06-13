"""``OpenAIClient`` — the OpenAI Chat Completions API as an ``LlmCall``.

Adapts OpenAI's request/response shape to the engine's Anthropic-style contract:
Anthropic tool specs → OpenAI ``tools`` (function calling), the system string →
a leading ``system`` message, and the response → a :class:`Message` with
``TextBlock`` / ``ToolUseBlock`` content and a mapped ``stop_reason``. Requires
the optional ``openai`` extra (``pip install agent-sdk[openai]``).
"""

from __future__ import annotations

import json
from typing import Any

from agent_sdk.clients.base import BaseClient, _env
from agent_sdk.clients.messages import Message, ProviderUsage, TextBlock, ToolUseBlock

__all__ = ["OpenAIClient"]

_FINISH_TO_STOP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
}


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
    def _to_openai_tools(tools: list[dict] | None) -> list[dict] | None:
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for t in tools
        ]

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
        oai_tools = self._to_openai_tools(tools)
        if oai_tools:
            kwargs["tools"] = oai_tools
        resp = await client.chat.completions.create(**kwargs)
        msg = self._adapt(resp)
        if count_usage:
            self._record(msg.usage)
        return msg

    @staticmethod
    def _adapt(resp: Any) -> Message:
        choice = resp.choices[0]
        m = choice.message
        blocks: list[Any] = []
        if getattr(m, "content", None):
            blocks.append(TextBlock(text=m.content))
        for tc in getattr(m, "tool_calls", None) or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            blocks.append(ToolUseBlock(id=tc.id, name=tc.function.name, input=args))
        if not blocks:
            blocks.append(TextBlock(text=""))
        stop = _FINISH_TO_STOP.get(choice.finish_reason or "stop", "end_turn")
        u = getattr(resp, "usage", None)
        usage = ProviderUsage(
            input_tokens=int(getattr(u, "prompt_tokens", 0) or 0) if u else 0,
            output_tokens=int(getattr(u, "completion_tokens", 0) or 0) if u else 0,
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
