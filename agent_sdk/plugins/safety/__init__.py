"""Safety capability plugin — the output-safety filter lobe.

Owns ``filter``: the output-contract lobe that gates the final message (refuse
unsafe content, redact, enforce policy on the way out). This is a **general**
safety concern — every agent wants it, RAG or not — so it is default-on and
independent of retrieval grounding (which lives in the separate :class:`RagPlugin`,
opt-in). Disable via a ``PluginRegistry`` only if an integrator owns output safety
elsewhere.
"""

from __future__ import annotations

from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.safety.lobes import filter as _filter

__all__ = ["SafetyPlugin"]


class SafetyPlugin:
    name = "safety"

    def lobes(self) -> list:
        return [_filter.LOBE]

    def install(self, setup: AgentSetup) -> None:
        for lb in self.lobes():
            setup.add_lobe(lb)
