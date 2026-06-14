"""``Lobes`` / ``Stages`` / ``Flows`` — the built-in PreAct network as namespaces.

The builtin default is the **faithfully-ported agent-core production network**
(18 lobes, 8 paths, the named flows — ``preact/production.py``). Each namespace
exposes ``.default()`` (the production network) and ``.minimal()`` (the small
qna/research/clarify network kept for lightweight agents/tests). Compose your own
by passing explicit lists to ``PreactAgent``.
"""

from __future__ import annotations

from agent_sdk.flow_def import Flow, flow
from agent_sdk.lobes.runtime import Lobe
from agent_sdk.preact.lobes import default_lobes
from agent_sdk.preact.production import production_flows, production_lobes, production_stages
from agent_sdk.stages import Stage, stage

__all__ = ["Lobes", "Stages", "Flows"]


class Lobes:
    @staticmethod
    def default() -> list[Lobe]:
        return production_lobes()

    @staticmethod
    def minimal() -> list[Lobe]:
        return default_lobes()


class Stages:
    @staticmethod
    def default() -> list[Stage]:
        return production_stages()

    @staticmethod
    def minimal() -> list[Stage]:
        return [
            stage(
                "plan",
                lobes=["plan"],
                loop="single",
                description="Decompose the question into sub-questions.",
            ),
            stage(
                "research",
                lobes=["research"],
                loop="agentic",
                description="Gather evidence with tools.",
            ),
            stage(
                "synthesize",
                lobes=["classify", "synthesize", "cite", "filter"],
                loop="single",
                description="Compose the grounded answer.",
            ),
            stage(
                "clarify",
                lobes=["clarify"],
                loop="single",
                use_when="an ambiguous follow-up",
                description="Ask one clarifying question.",
            ),
        ]


class Flows:
    @staticmethod
    def default() -> list[Flow]:
        return production_flows()

    @staticmethod
    def minimal() -> list[Flow]:
        return [
            flow(
                "research",
                use_when="multi-step questions needing sources",
                stages=["plan", "research", "synthesize"],
                threshold=0.5,
                signal={
                    "any": [
                        {"lexical": ["compare", "vs", "versus", "research", "analyze"]},
                        {"min_words": 12},
                    ]
                },
            ),
            flow(
                "clarify",
                use_when="an ambiguous follow-up",
                stages=["clarify"],
                threshold=0.5,
                grounds=False,
                signal={"flag": "ambiguous"},
            ),
            flow(
                "qna",
                use_when="a direct question",
                stages=["synthesize"],
                threshold=0.4,
                signal={"const": 0.5},
            ),
        ]
