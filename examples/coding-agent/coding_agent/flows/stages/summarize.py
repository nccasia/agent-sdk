"""summarize stage — report what changed (files + test result) (no tools)."""

from __future__ import annotations

from agent_sdk import stage

STAGE = stage(
    "summarize", lobes=["summarize"], loop="single",
    description="Report what changed (files + test result).",
)
