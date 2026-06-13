"""Stage + flow owned by the support-triage plugin."""

from __future__ import annotations

from agent_sdk.flow_def import Flow, flow
from agent_sdk.plugins.support_triage.lobes import TRIAGE_CUES
from agent_sdk.stages import Stage, stage

__all__ = ["triage_stage", "triage_flow"]


def triage_stage() -> Stage:
    """Agentic stage so the model can call ``lookup_ticket`` while triaging."""
    return stage(
        "triage",
        lobes=["triage"],
        loop="agentic",
        tools=["lookup_ticket"],
        description="Assess urgency, look up the ticket, and route it.",
    )


def triage_flow() -> Flow:
    """Intent path recognized on the urgency cues."""
    return flow(
        "triage",
        use_when="an urgent support ticket or incident",
        stages=["triage"],
        threshold=0.5,
        signal={"any": [{"lexical": list(TRIAGE_CUES)}]},
    )
