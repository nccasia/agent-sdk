"""explore stage — navigate + read the codebase to ground the work."""

from __future__ import annotations

from agent_sdk import stage

from coding_agent.flows.stages._slices import READ_TOOLS

STAGE = stage(
    "explore", lobes=["triage", "explore"], loop="agentic", tools=READ_TOOLS,
    description="Navigate + read the codebase to ground the work.", hops=50,
)
