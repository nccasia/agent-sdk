"""LLM clients — concrete ``LlmCall`` implementations (multi-provider).

The model is a *client class*, not a fixed string. ``AnthropicClient`` /
``OpenAIClient`` own streaming, usage accounting, and retries; ``MixedClient``
dispatches per stage; ``FakeClient`` is a deterministic in-memory client for
tests and local dev. A bare ``"claude-…"`` / ``"gpt-…"`` string is shorthand
(``make_client``) that builds the matching default client.
"""

from __future__ import annotations

from agent_sdk.clients.anthropic_client import AnthropicClient
from agent_sdk.clients.base import BaseClient, make_client
from agent_sdk.clients.fake import FakeClient
from agent_sdk.clients.messages import Message, ProviderUsage, TextBlock, ToolUseBlock
from agent_sdk.clients.minimax_client import MiniMaxClient
from agent_sdk.clients.mixed import MixedClient
from agent_sdk.clients.openai_client import OpenAIClient

__all__ = [
    "BaseClient",
    "make_client",
    "AnthropicClient",
    "MiniMaxClient",
    "OpenAIClient",
    "MixedClient",
    "FakeClient",
    "Message",
    "ProviderUsage",
    "TextBlock",
    "ToolUseBlock",
]
