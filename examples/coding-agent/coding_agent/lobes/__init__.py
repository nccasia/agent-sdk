"""Production-shaped coding lobes (the OY context axis).

Each lobe is a small, self-describing context worker: metadata + a deterministic
free activation + a system-prompt contribution. They encode a coding agent's
disciplines — explore before editing, plan multi-step work, write code that
matches surrounding style, verify with the real test suite, report honestly —
grouped by network layer:

- :mod:`coding_agent.lobes.cognition` — triage, explore, plan, implement, surveyor
- :mod:`coding_agent.lobes.expression` — verify, summarize, documenter
"""

from __future__ import annotations

from agent_sdk import Lobe

from coding_agent.lobes.cognition import Explore, Implement, Plan, Surveyor, Triage
from coding_agent.lobes.expression import Documenter, Summarize, Verify


def coding_lobes() -> list[Lobe]:
    return [Triage(), Explore(), Plan(), Implement(), Verify(), Summarize(),
            Surveyor(), Documenter()]


__all__ = [
    "coding_lobes",
    "Triage", "Explore", "Plan", "Implement", "Surveyor",
    "Verify", "Summarize", "Documenter",
]
