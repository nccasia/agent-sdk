"""verify lobe — run the real test suite and report honestly."""

from __future__ import annotations

from agent_sdk import Layer, Lobe


class Verify(Lobe):
    id = "verify"
    name = "Verify"
    description = "Run the real test suite / build and report the result honestly."
    use_when = "after making a change"
    layer = Layer.EXPRESSION
    behavior = "verify"
    order = 8
    system_prompt = (
        "Run the project's tests (or the most relevant subset) with Bash and "
        "read the output. If anything fails, fix it. Report pass/fail truthfully — "
        "never claim success you did not observe."
    )

    def activation(self, ctx: dict) -> float:
        return 0.0  # lit by the feature/quick_fix flows' lobe bias
