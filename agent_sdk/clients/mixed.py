"""``MixedClient`` — route each call to a per-stage (per-provider) client.

A composite ``LlmCall`` that dispatches on the call's ``stage``: a cheap tier to
classify, a strong tier to synthesize, etc. Unmapped stages fall back to
``default``.

    MixedClient(
        default=AnthropicClient("claude-opus-4-6"),
        classify=OpenAIClient("gpt-4o-mini"),
        synthesize=AnthropicClient("claude-opus-4-6"),
    )
"""

from __future__ import annotations

from typing import Any

from agent_sdk.clients.base import BaseClient, make_client
from agent_sdk.clients.messages import ProviderUsage

__all__ = ["MixedClient"]


class MixedClient(BaseClient):
    provider = "mixed"

    def __init__(self, default: Any, **per_stage: Any):
        self.default = make_client(default)
        self.by_stage: dict[str, Any] = {k: make_client(v) for k, v in per_stage.items()}
        super().__init__(getattr(self.default, "model", "mixed"))

    def client_for(self, stage: str) -> Any:
        return self.by_stage.get(stage, self.default)

    @property
    def total_usage(self) -> ProviderUsage:  # type: ignore[override]
        total = ProviderUsage()
        for c in (self.default, *self.by_stage.values()):
            u = getattr(c, "total_usage", None)
            if isinstance(u, ProviderUsage):
                total = total + u
        return total

    @total_usage.setter
    def total_usage(self, _value: ProviderUsage) -> None:
        # Sub-clients own their accounting; the aggregate is computed on read.
        pass

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
    ) -> Any:
        client = self.client_for(stage)
        return await client(
            stage=stage,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            count_usage=count_usage,
        )
