"""document stage — aggregate findings + write the architecture document."""

from __future__ import annotations

from agent_sdk import stage

from coding_agent.flows.stages._slices import DOC_TOOLS

STAGE = stage(
    "document", lobes=["documenter"], loop="agentic", tools=DOC_TOOLS,
    description="Aggregate findings + write the architecture document.", hops=50,
    max_tokens=8000,  # the architecture doc is large — fit it in one call
)
