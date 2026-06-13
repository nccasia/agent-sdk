"""plan lobe — decompose a multi-step change into ordered steps."""

from __future__ import annotations

from agent_sdk import Layer, Lobe


class Plan(Lobe):
    id = "plan"
    name = "Plan"
    description = "Decompose a multi-step change into concrete, ordered steps."
    use_when = "a feature or refactor that needs more than one edit"
    layer = Layer.COGNITION
    behavior = "decompose"
    order = 2
    system_prompt = (
        "Lay out the few concrete steps this change needs (which files, which "
        "functions, which tests). Keep it minimal — the smallest change that "
        "correctly does the job. Save the plan to memory (action=remember, "
        "scope=conversation, key=plan) so you can track progress across many steps "
        "without losing the thread."
    )

    def activation(self, ctx: dict) -> float:
        return 0.0  # lit by the feature flow's lobe bias
