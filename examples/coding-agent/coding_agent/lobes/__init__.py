"""Production-shaped coding lobes (the OY context axis) — one file per lobe.

Each lobe is a small, self-describing context worker: metadata + a deterministic
free activation + a system-prompt contribution. They encode a coding agent's
disciplines — explore before editing, plan multi-step work, write code that
matches surrounding style, verify with the real test suite, report honestly.

``triage`` / ``explore`` / ``summarize`` are always-on (their slices appear in
most stages); the rest are lit by their flow's lobe bias so they only contribute
on the stages that consult them. Open the file named for a lobe to read it.
"""

from __future__ import annotations

from agent_sdk import Lobe

from coding_agent.lobes.documenter import Documenter
from coding_agent.lobes.explore import Explore
from coding_agent.lobes.implement import Implement
from coding_agent.lobes.plan import Plan
from coding_agent.lobes.summarize import Summarize
from coding_agent.lobes.surveyor import Surveyor
from coding_agent.lobes.triage import Triage
from coding_agent.lobes.verify import Verify


def coding_lobes() -> list[Lobe]:
    return [Triage(), Explore(), Plan(), Implement(), Verify(), Summarize(),
            Surveyor(), Documenter()]


__all__ = [
    "coding_lobes",
    "Triage", "Explore", "Plan", "Implement", "Surveyor",
    "Verify", "Summarize", "Documenter",
]
