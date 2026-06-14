"""``PluginGuardrails`` — pre/post turn checks (a guardrail raises to block).

The plugin is the seam; ``make_answer_leak_check`` is the built-in deterministic
post-check (secret / bulk-PII / forbidden-substring / impossible-action scan) that
most agents want.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.guardrails.errors import GuardrailError
from agent_sdk.plugins.guardrails.leak_guard import make_answer_leak_check

__all__ = ["PluginGuardrails", "GuardrailError", "make_answer_leak_check"]


class PluginGuardrails:
    name = "guardrails"

    def __init__(
        self,
        *,
        pre: list[Callable[[str], Any]] | None = None,
        post: list[Callable[[Any], Any]] | None = None,
    ):
        self.pre = list(pre or [])
        self.post = list(post or [])

    def install(self, setup: AgentSetup) -> None:
        for check in self.pre:
            setup.add_pre_check(check)
        for check in self.post:
            setup.add_post_check(check)
