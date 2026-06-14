"""Flow-axis stage definitions.

These modules are the OX axis: progressive, inspectable stages (over time) that
consult the OY lobe axis for context-window content.
"""

from __future__ import annotations

from agent_sdk.flows.stages.act import act
from agent_sdk.flows.stages.catalog import (
    catalog_menu,
    get_stage,
    stage_catalog,
)
from agent_sdk.flows.stages.cite import research_cite
from agent_sdk.flows.stages.common import Stage
from agent_sdk.flows.stages.filter import research_filter
from agent_sdk.flows.stages.research import research_investigate
from agent_sdk.flows.stages.respond import respond_step
from agent_sdk.flows.stages.synthesize import (
    clarify_synthesize,
    fallback_synthesize,
    onboarding_synthesize,
    qna_synthesize,
    relational_synthesize,
    research_synthesize,
)

__all__ = [
    "Stage",
    "act",
    "stage_catalog",
    "catalog_menu",
    "get_stage",
    "clarify_synthesize",
    "fallback_synthesize",
    "research_investigate",
    "onboarding_synthesize",
    "qna_synthesize",
    "relational_synthesize",
    "research_cite",
    "research_filter",
    "research_synthesize",
    "respond_step",
]
