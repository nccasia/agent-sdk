"""Citation-stage definitions for the OX flow axis."""

from __future__ import annotations

from agent_sdk.flows.flow import FlowStep
from agent_sdk.flows.stages.common import Stage


class ResearchCite(Stage):
    """Citation grounding — attach evidence-channel citations to the answer.

    A terminal output-contract stage (pinned grounding lobe). Runs at
    temperature 0; part of the ground-or-refuse safety contract.
    """

    id = "cite"
    flow = "research"
    description = "research: citation grounding (pinned)"
    use_when = "an answer was composed and must be grounded in cited evidence"
    how = "single call binding the answer to the pipeline's read evidence (cite lobe)"
    loop = "single"
    lobes = ("cite",)


def research_cite() -> FlowStep:
    return ResearchCite().spec
