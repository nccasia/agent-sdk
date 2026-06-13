"""surveyor lobe — map a large codebase's structure breadth-first."""

from __future__ import annotations

from agent_sdk import Layer, Lobe


class Surveyor(Lobe):
    id = "surveyor"
    name = "Surveyor"
    description = "Map a large codebase's structure breadth-first before diving in."
    use_when = "understanding a whole system"
    layer = Layer.COGNITION
    behavior = "gather"
    order = 1
    system_prompt = (
        "Map the repository top-down before diving deep. Use LS on the root "
        "and key directories, Glob for the dominant file types and entry points "
        "(README, pyproject/package.json, __init__/main/index), and Grep for the "
        "high-level wiring. Build a mental table of contents — the subsystems, "
        "where each lives, and how they connect — so the plan can target them."
    )

    def activation(self, ctx: dict) -> float:
        return 0.0  # lit by the understand flow's lobe bias
