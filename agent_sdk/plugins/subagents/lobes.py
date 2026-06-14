"""Subagent-catalog lobe (OY axis) — render the named subagents available to delegate to.

The deterministic analogue of Claude Code surfacing agent *descriptions* so the model knows
which named subagents it may invoke. It renders the registry's ``name — description`` listing
into the reflect step's context; the model names one in ``meta_control(action=fan_out)`` and
the enactor resolves it. Contributes nothing when the registry is empty (harmless anywhere).
"""

from __future__ import annotations

from agent_sdk.contracts.turn import PromptContribution, TurnContext
from agent_sdk.lobes.runtime import Lobe
from agent_sdk.network.activation import LAYER_COGNITION

__all__ = ["SubagentCatalogLobe"]


class SubagentCatalogLobe(Lobe):
    """Render the registry's subagent catalog so the reflect step can delegate by name."""

    id = "subagent_catalog"
    name = "Subagent Catalog"
    description = "Lists the named subagents the agent can delegate to via meta_control fan_out."
    use_when = "the agent is deciding whether to delegate part of the task to a subagent"
    how = "renders the SubagentRegistry's name+description listing as a context block"
    layer = LAYER_COGNITION
    behavior = "select"
    prior = 1.0  # active wherever a meta stage lists it; prompt() is empty without a catalog

    def __init__(self, registry: object | None = None):
        self._registry = registry

    def activation(self, ctx: dict) -> float:
        return 1.0

    def prompt(self, ctx: TurnContext) -> list[PromptContribution]:
        reg = self._registry
        catalog = reg.render_catalog() if reg is not None else ""
        if not catalog:
            return []
        return [PromptContribution(catalog, stability="stable", source=self.id)]
