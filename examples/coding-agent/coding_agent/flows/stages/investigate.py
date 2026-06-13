"""investigate stage — read each subsystem, save findings to memory."""

from __future__ import annotations

from agent_sdk import stage

from coding_agent.flows.stages._slices import NOTE_TOOLS

STAGE = stage(
    "investigate", lobes=["explore"], loop="agentic", tools=NOTE_TOOLS,
    description="Follow the plan: read each subsystem, save findings to memory.",
    hops=80,  # stall-break ends early when exploration stops making progress
)
