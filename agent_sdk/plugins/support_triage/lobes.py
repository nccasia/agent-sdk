"""Lobes owned by the support-triage plugin."""

from __future__ import annotations

from agent_sdk.lobes.runtime import Lobe
from agent_sdk.network.activation import LAYER_COGNITION

__all__ = ["TriageLobe", "TRIAGE_CUES"]

# Lexical cues that mark an urgent support turn — shared by the lobe activation and the flow
# recognizer so both light on the same signal (deterministic, free).
TRIAGE_CUES = ("urgent", "incident", "escalate", "ticket", "outage", "down", "p0", "p1")


class TriageLobe(Lobe):
    """Cognition-layer lobe that frames the turn as triage when urgency cues fire."""

    id = "triage"
    name = "Triage"
    description = "Assess urgency and route a support ticket/incident."
    use_when = "an urgent support ticket or incident report"
    layer = LAYER_COGNITION
    behavior = "select"
    system_prompt = (
        "Assess the severity of the reported issue, identify the affected service, "
        "and state the next routing action concisely."
    )

    def activation(self, ctx: dict) -> float:
        q = str(ctx.get("query", "")).lower()
        return 1.0 if any(c in q for c in TRIAGE_CUES) else 0.0
