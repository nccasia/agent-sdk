"""Worked example plugin — a *full-surface* first-class plug-and-play component.

``PluginSupportTriage`` is the canonical reference: one plugin that contributes **every**
capacity kind at once — a lobe, a stage, a flow (intent path), a skill, and a tool — so a host
gains a whole "urgent support triage" behavior by adding a single entry to ``plugins=[…]``. Drop
it and the behavior is gone.

The plugin OWNS its code, co-located in this folder (one file per kind): ``lobes.py`` /
``stages.py`` / ``skills.py`` / ``tools.py``. This thin ``__init__`` just assembles them.

    agent = PreactAgent(client=…, plugins=[PluginSupportTriage()])
    # "this incident is urgent, escalate ticket 412" → routes to the triage flow, the triage
    # lobe lights, the triage_policy skill is in context, and lookup_ticket is callable.
"""

from __future__ import annotations

from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.support_triage.lobes import TriageLobe
from agent_sdk.plugins.support_triage.skills import triage_skill
from agent_sdk.plugins.support_triage.stages import triage_flow, triage_stage
from agent_sdk.plugins.support_triage.tools import lookup_ticket

__all__ = ["PluginSupportTriage", "lookup_ticket"]


class PluginSupportTriage:
    """Full-surface example plugin (lobe + stage + flow + skill + tool)."""

    name = "support_triage"
    enabled = True

    def install(self, setup: AgentSetup) -> None:
        setup.add_tool(lookup_ticket)
        setup.add_lobe(TriageLobe())
        setup.add_stage(triage_stage())
        setup.add_flow(triage_flow())
        setup.add_skill(triage_skill())
