"""implement lobe — write minimal, correct code that matches the style."""

from __future__ import annotations

from agent_sdk import Layer, Lobe


class Implement(Lobe):
    id = "implement"
    name = "Implement"
    description = "Write minimal, correct code that matches the surrounding style."
    use_when = "making the change"
    layer = Layer.COGNITION
    behavior = "compose"
    order = 3
    system_prompt = (
        "Make the change with Edit (exact string match — Read first so the match is "
        "exact) or Write for new files. Match the existing code's style, naming, and "
        "idioms. Change as little as possible. Add or update tests for what you "
        "changed. Do not leave the tree broken. As you complete plan steps, update "
        "the plan in memory."
    )

    def activation(self, ctx: dict) -> float:
        return 0.0  # lit by the feature/quick_fix flows' lobe bias
