"""triage lobe — classify the request (a question, a fix, or a feature)."""

from __future__ import annotations

from agent_sdk import Layer, Lobe


class Triage(Lobe):
    id = "triage"
    name = "Triage"
    description = "Classify the request: a question, a quick fix, or a feature."
    use_when = "every coding turn"
    layer = Layer.COGNITION
    behavior = "select"
    system_prompt = (
        "You are a careful senior software engineer working in a real repository. "
        "First understand exactly what is being asked: is it a question about the "
        "code, a small fix, or a multi-step change? Match your effort to the task."
    )

    def activation(self, ctx: dict) -> float:
        return 1.0
