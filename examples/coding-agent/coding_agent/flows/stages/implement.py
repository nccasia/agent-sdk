"""implement stage — make the change on disk."""

from __future__ import annotations

from agent_sdk import stage

from coding_agent.flows.stages._slices import EDIT_TOOLS

STAGE = stage(
    "implement", lobes=["implement"], loop="agentic", tools=EDIT_TOOLS,
    description="Make the change on disk.", hops=80,
)
