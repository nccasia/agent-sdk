"""Skills owned by the support-triage plugin."""

from __future__ import annotations

from agent_sdk.skill_def import Skill

__all__ = ["triage_skill"]


def triage_skill() -> Skill:
    return Skill(
        id="triage_policy",
        when="handling an urgent support ticket or incident",
        disclosure="eager",
        instructions=(
            "Severity rubric: P0 = full outage, P1 = major degradation, P2 = minor. "
            "Always restate the ticket id, the severity, and the immediate owner."
        ),
        tools=["lookup_ticket"],
    )
