"""Provider-agnostic message shape the engine consumes.

The engine reads exactly four things off a model response: the content blocks
(``text`` and ``tool_use``), the ``stop_reason``, and token ``usage``. The
Anthropic SDK already returns objects with this duck shape, so ``AnthropicClient``
hands its raw response straight through; ``FakeClient`` and ``OpenAIClient``
construct these dataclasses to match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["TextBlock", "ToolUseBlock", "ProviderUsage", "Message"]


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: ProviderUsage) -> ProviderUsage:
        return ProviderUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass
class Message:
    content: list[Any]
    stop_reason: str = "end_turn"
    usage: ProviderUsage = field(default_factory=ProviderUsage)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.content if getattr(b, "type", None) == "text")

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if getattr(b, "type", None) == "tool_use"]
