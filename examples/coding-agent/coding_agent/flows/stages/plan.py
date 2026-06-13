"""plan stage — decompose a multi-step change into ordered steps (no tools)."""

from __future__ import annotations

from agent_sdk import stage

STAGE = stage(
    "plan", lobes=["plan"], loop="single",
    description="Decompose a multi-step change into ordered steps.",
)
