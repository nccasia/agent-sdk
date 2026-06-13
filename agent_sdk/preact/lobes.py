"""The built-in default lobes (the PreAct B2..B5 network).

Each lobe is a small, self-describing context worker authored with the ``Lobe``
class: metadata + one deterministic free activation + a system-prompt
contribution. ``cite`` / ``filter`` are the output-contract lobes (``PINNED_LOBES``)
— the activation network can never deactivate them on a grounding flow.
"""

from __future__ import annotations

from agent_sdk.lobes.runtime import Lobe
from agent_sdk.network.activation import LAYER_COGNITION, LAYER_EXPRESSION


class Classify(Lobe):
    id = "classify"
    name = "Classify"
    description = "Route the turn: a direct answer vs. multi-step research."
    use_when = "every answer-producing turn"
    layer = LAYER_COGNITION
    behavior = "select"
    system_prompt = (
        "First decide whether the question can be answered directly or needs "
        "multi-step research. Keep the answer focused and concrete."
    )

    def activation(self, ctx: dict) -> float:
        return 1.0


class Plan(Lobe):
    id = "plan"
    name = "Plan"
    description = "Decompose a complex question into sub-questions."
    use_when = "a multi-step question that needs a plan"
    layer = LAYER_COGNITION
    behavior = "decompose"
    order = 1
    system_prompt = (
        "Break the request into the few concrete sub-questions you must answer, "
        "then proceed to gather what each needs."
    )

    def activation(self, ctx: dict) -> float:
        return 0.0  # lit by the research flow's lobe bias


class Research(Lobe):
    id = "research"
    name = "Research"
    description = "Gather evidence from tools/sources before answering."
    use_when = "the question needs external facts"
    layer = LAYER_COGNITION
    behavior = "gather"
    order = 2
    system_prompt = (
        "Use the available tools to gather the facts you need. Prefer a short, "
        "grounded answer over a broad speculative one; cite what you used."
    )

    def activation(self, ctx: dict) -> float:
        return 0.0  # lit by the research flow's lobe bias


class Synthesize(Lobe):
    id = "synthesize"
    name = "Synthesize"
    description = "Compose the final answer from what was gathered."
    use_when = "producing the answer"
    layer = LAYER_COGNITION
    behavior = "compose"
    order = 3
    system_prompt = "Write a clear, direct answer. Be concrete and avoid filler."

    def activation(self, ctx: dict) -> float:
        return 1.0


class Clarify(Lobe):
    id = "clarify"
    name = "Clarify"
    description = "Ask one focused clarifying question when the turn is ambiguous."
    use_when = "an ambiguous follow-up"
    layer = LAYER_COGNITION
    behavior = "clarify"
    order = 4
    system_prompt = (
        "The request is ambiguous. Ask exactly one concise clarifying question instead of guessing."
    )

    def activation(self, ctx: dict) -> float:
        return 0.0  # lit by the clarify flow's lobe bias


class Cite(Lobe):
    id = "cite"
    name = "Cite"
    description = "Attach grounding to claims (the output contract)."
    use_when = "grounding (KB-answering) turns"
    layer = LAYER_EXPRESSION
    behavior = "ground"
    order = 8
    system_prompt = (
        "Ground factual claims in the sources you used. If you cannot ground a "
        "claim, do not assert it."
    )

    def activation(self, ctx: dict) -> float:
        return 0.0  # output-contract: driven by the flow's `grounds` flag


class Filter(Lobe):
    id = "filter"
    name = "Filter"
    description = "Refuse rather than emit ungrounded claims (ground-or-refuse)."
    use_when = "grounding (KB-answering) turns"
    layer = LAYER_EXPRESSION
    behavior = "filter"
    order = 9
    system_prompt = (
        "If the gathered evidence does not support an answer, say you cannot "
        "confirm it from the available sources rather than guessing."
    )

    def activation(self, ctx: dict) -> float:
        return 0.0  # output-contract: driven by the flow's `grounds` flag


def default_lobes() -> list[Lobe]:
    return [Classify(), Plan(), Research(), Synthesize(), Clarify(), Cite(), Filter()]
