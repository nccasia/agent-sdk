"""verify stage — run the tests and fix failures."""

from __future__ import annotations

from agent_sdk import stage

from coding_agent.flows.stages._slices import VERIFY_TOOLS

STAGE = stage(
    "verify", lobes=["verify"], loop="agentic", tools=VERIFY_TOOLS,
    description="Run the tests and fix failures.", hops=40,
)
