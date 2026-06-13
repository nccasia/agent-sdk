"""answer stage — deeply explore, then answer a question about the code."""

from __future__ import annotations

from agent_sdk import stage

from coding_agent.flows.stages._slices import READ_TOOLS

STAGE = stage(
    "answer", lobes=["triage", "explore", "summarize"], loop="agentic", tools=READ_TOOLS,
    description="Deeply explore, then answer a question about the code.",
    hops=80,  # stall-break ends early when exploration stops making progress
)
