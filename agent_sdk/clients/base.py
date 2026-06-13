"""``BaseClient`` — the LLM-client base + the ``client="claude-…"`` shorthand.

A client is a concrete :class:`~agent_sdk.contracts.llm.LlmCall`: one async
``__call__`` that performs one model call and returns a provider message. Clients
own streaming, retries, and usage accounting. Subclass :class:`BaseClient` or
implement the ``LlmCall`` protocol directly.
"""

from __future__ import annotations

import os
from typing import Any

from agent_sdk.clients.messages import Message, ProviderUsage

__all__ = ["BaseClient", "make_client"]


class BaseClient:
    """Common base for provider clients.

    Holds the model id and a per-process usage accumulator. Subclasses implement
    the async ``__call__`` with the ``LlmCall`` signature.
    """

    provider: str = "base"

    def __init__(self, model: str, *, api_key: str | None = None, base_url: str | None = None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.total_usage = ProviderUsage()

    def model_for(self, stage: str) -> str:  # noqa: ARG002 - overridden by MixedClient
        return self.model

    def _record(self, usage: ProviderUsage) -> None:
        self.total_usage = self.total_usage + usage

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
    ) -> Message:  # pragma: no cover - abstract
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"{type(self).__name__}(model={self.model!r})"


def make_client(spec: Any) -> Any:
    """Resolve a client from a string shorthand or pass an instance through.

    ``"claude-…"`` / ``"opus…"`` / ``"sonnet…"`` / ``"haiku…"`` → ``AnthropicClient``;
    ``"gpt-…"`` / ``"o1…"`` / ``"o3…"`` → ``OpenAIClient``; ``"minimax-…"`` /
    ``"abab-…"`` → ``MiniMaxClient``. An object that is already callable
    (implements ``LlmCall``) is returned unchanged.
    """
    if spec is None:
        raise ValueError("a client is required")
    if isinstance(spec, str):
        low = spec.lower()
        if low.startswith(("gpt", "o1", "o3", "o4")):
            from agent_sdk.clients.openai_client import OpenAIClient

            return OpenAIClient(spec)
        if low.startswith(("minimax", "abab")):
            from agent_sdk.clients.minimax_client import MiniMaxClient

            return MiniMaxClient(spec)
        from agent_sdk.clients.anthropic_client import AnthropicClient

        return AnthropicClient(spec)
    return spec


def _env(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None
