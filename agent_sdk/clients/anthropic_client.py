"""``AnthropicClient`` — the Anthropic Messages API as an ``LlmCall``.

Wraps ``anthropic.AsyncAnthropic``. Honors the project's MiniMax-compatible
environment (``ANTHROPIC_BASE_URL`` + ``ANTHROPIC_AUTH_TOKEN``) as well as the
standard ``ANTHROPIC_API_KEY``. The raw Anthropic response already has the
``.content`` / ``.stop_reason`` / ``.usage`` shape the engine consumes, so it is
returned as-is; usage is also recorded on the client for accounting.
"""

from __future__ import annotations

from typing import Any

from agent_sdk.clients.base import BaseClient, _env
from agent_sdk.clients.messages import ProviderUsage

__all__ = ["AnthropicClient"]


class AnthropicClient(BaseClient):
    provider = "anthropic"

    def __init__(
        self,
        model: str = "claude-opus-4-6",
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int = 2,
        timeout: float = 300.0,
    ):
        super().__init__(model, api_key=api_key, base_url=base_url)
        self.max_retries = max_retries
        # Per-request timeout (seconds). The anthropic SDK default is 600s, so a
        # stalled provider response blocks ~10 min per call (×retries) — a long
        # agentic run then appears to hang. A finite timeout makes a stall fail
        # fast (then retry / surface as an error) instead of hanging the turn.
        self.timeout = timeout
        self._client: Any = None

    def _ensure(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic(
                api_key=self.api_key or _env("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"),
                base_url=self.base_url or _env("ANTHROPIC_BASE_URL"),
                max_retries=self.max_retries,
                timeout=self.timeout,
            )
        return self._client

    @staticmethod
    def _usage(resp: Any) -> ProviderUsage:
        u = getattr(resp, "usage", None)
        if u is None:
            return ProviderUsage()
        return ProviderUsage(
            input_tokens=int(getattr(u, "input_tokens", 0) or 0),
            output_tokens=int(getattr(u, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(u, "cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        )

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
        client = self._ensure()
        kwargs: dict[str, Any] = {
            "model": self.model_for(stage),
            "system": system,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.0 if temperature is None else temperature,
        }
        if tools:
            kwargs["tools"] = tools
        resp = await client.messages.create(**kwargs)
        if count_usage:
            self._record(self._usage(resp))
        return self._postprocess(resp)

    def _postprocess(self, resp: Any) -> Any:
        """Hook for provider-specific response normalization.

        The base Anthropic client is a faithful passthrough — the raw response
        already has the ``.content`` / ``.stop_reason`` / ``.usage`` shape the
        engine consumes. Subclasses (e.g. :class:`MiniMaxClient`) override this to
        repair provider quirks such as markup-emitted tool calls.
        """
        return resp
