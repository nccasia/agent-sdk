"""Shared Context — one handle, every component, every scope.

The single access surface over an agent's state: scoped get/set/delete/search
from the turn (RAM) out to the bot (durable), plus the ambient read-only turn
facts (identity / channel / session / evidence). Reached by lobes, tools, and
skills as ONE object so they share one view.

See ``docs/concepts/07-shared-context.md``. Backend + value model:
``docs/concepts/06-universal-memory.md``.
"""

from __future__ import annotations

from agent_sdk.context.context import (
    AgentContext,
    Evidence,
    Scope,
    bind_context,
    current_context,
)

__all__ = ["AgentContext", "Evidence", "Scope", "bind_context", "current_context"]
