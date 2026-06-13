"""Safety capability plugin — the grounding output-contract lobes.

Owns ``cite`` (citation grounding) and ``filter`` (ground-or-refuse). Part of the default
capability set. Disabling it (via a ``PluginRegistry``) turns off grounding — citations-mandatory
is then the caller's responsibility.
"""

from __future__ import annotations

from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.safety.lobes import cite as _cite
from agent_sdk.plugins.safety.lobes import filter as _filter

__all__ = ["SafetyPlugin"]


class SafetyPlugin:
    name = "safety"

    def lobes(self) -> list:
        return [_cite.LOBE, _filter.LOBE]

    def install(self, setup: AgentSetup) -> None:
        for lb in self.lobes():
            setup.add_lobe(lb)
