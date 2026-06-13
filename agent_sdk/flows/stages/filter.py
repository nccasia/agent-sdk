"""Filter-stage definitions for the OX flow axis."""

from __future__ import annotations

from agent_sdk.flows.flow import FlowStep
from agent_sdk.flows.stages.common import Stage


class ResearchFilter(Stage):
    """Ground-or-refuse filter — the grounding output-contract terminal stage.

    Refuses rather than ship an ungrounded claim (``refuse_if: no_citations``).
    Pinned + temperature 0; the ground-or-refuse safety floor, never a
    metacognition decision.
    """

    id = "filter"
    flow = "research"
    description = "research: ground-or-refuse filter (grounding output-contract)"
    use_when = "the composed+cited answer must be verified to ship or refuse"
    how = "single call enforcing refuse_if=no_citations + answer guards (filter lobe)"
    loop = "single"
    lobes = ("filter",)


def research_filter() -> FlowStep:
    return ResearchFilter().spec
