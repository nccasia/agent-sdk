"""``PluginGuardrails`` — pre/post turn checks (a guardrail raises to block)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_sdk.plugins.base import AgentSetup

__all__ = ["PluginGuardrails", "GuardrailError"]


class GuardrailError(Exception):
    """Raised by a guardrail check to block a turn."""


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
