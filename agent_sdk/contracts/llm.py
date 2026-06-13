"""The LLM-call seam — one narrow, injectable protocol a lobe behavior calls.

A lobe behavior executes against ``LlmCall`` so it is unit-testable with a
``FakeLlm`` and optimizable without constructing the interpreter. The
production implementation (``BotPolicyInterpreter._lobe_llm``) wraps per-stage
model resolution + ``client.messages.create`` + usage roll-up.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Protocol


class LlmCall(Protocol):
    """One LLM call on behalf of a lobe behavior.

    ``stage`` selects the policy stage whose model config applies; the
    implementation resolves the model and accounts usage. ``system`` may be a
    plain string or a cache-split block array.
    """

    def __call__(
        self,
        *,
        stage: str,
        system: str | list,
        messages: list[dict],
        max_tokens: int,
        temperature: float | None = None,
        tools: list[dict] | None = None,
        count_usage: bool = True,
    ) -> Awaitable[Any]: ...
