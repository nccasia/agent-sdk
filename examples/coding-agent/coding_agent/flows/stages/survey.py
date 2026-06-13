"""survey stage — map the repository structure top-down."""

from __future__ import annotations

from agent_sdk import stage

from coding_agent.flows.stages._slices import READ_TOOLS

STAGE = stage(
    "survey", lobes=["triage", "surveyor"], loop="agentic", tools=READ_TOOLS,
    description="Map the repository structure top-down.", hops=40,
)
