"""summarize lobe — state concisely what changed + the test result."""

from __future__ import annotations

from agent_sdk import Layer, Lobe


class Summarize(Lobe):
    id = "summarize"
    name = "Summarize"
    description = "State concisely what changed (files touched) and the test result."
    use_when = "producing the final reply"
    layer = Layer.EXPRESSION
    behavior = "compose"
    order = 9
    system_prompt = (
        "Summarize for a reviewer: what you changed, which files, and the test "
        "result. Be concrete and brief. If you could not complete the task, say so "
        "plainly and explain what is blocking."
    )

    def activation(self, ctx: dict) -> float:
        return 1.0
