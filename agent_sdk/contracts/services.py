"""Injected side-effect seams available to executable lobe classes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent_sdk.contracts.llm import LlmCall


@dataclass
class LobeServices:
    """Injected side-effect seams available to executable lobe classes."""

    llm: LlmCall | None = None
    execute_tools: Callable[..., Awaitable[Any]] | None = None
    embed: Callable[..., Awaitable[Any]] | Callable[..., Any] | None = None
    post_internal_context: Callable[..., Awaitable[Any]] | None = None
    session_factory: Callable[..., Any] | None = None
    redis: Any = None
    emit: Callable[..., Awaitable[Any]] | None = None
