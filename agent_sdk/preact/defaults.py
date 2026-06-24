"""``Lobes`` / ``Stages`` / ``Flows`` — the built-in PreAct network as namespaces.

The builtin default is the **faithfully-ported agent-core production network**
(18 lobes, 8 paths, the named flows — ``preact/production.py``). Each namespace
exposes ``.default()`` (the production network), ``.minimal()`` (the small
qna/research/clarify network kept for lightweight agents/tests), and ``.chat()``
(a single-flow casual-conversation network — ``classify → synthesize`` + safety,
no retrieval/skills/tasks/memory — for chit-chat / persona bots). Compose your
own by passing explicit lists to ``PreactAgent``.
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

    @staticmethod
    def chat() -> list[Lobe]:
        """A casual-conversation lobe set — route, generate, and safety only
        (no retrieval/skills/tools/tasks/memory lobes). A voice/respond plugin's
        lobe is added on top when present."""
        keep = {"classify", "synthesize", "filter"}
        return [lobe for lobe in default_lobes() if getattr(lobe, "id", "") in keep]


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

    @staticmethod
    def chat() -> list[Stage]:
        """A single generating stage for casual chat — no plan/research/cite."""
        return [
            stage(
                "synthesize",
                lobes=["classify", "synthesize", "filter"],
                loop="single",
                description="Compose one short, casual reply (no retrieval).",
            )
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

    @staticmethod
    def chat() -> list[Flow]:
        """A single casual-conversation flow: every turn goes straight to the
        generating stage (ungrounded — no sources needed for chit-chat)."""
        return [
            flow(
                "chat",
                use_when="casual conversation / chit-chat (no sources needed)",
                stages=["synthesize"],
                threshold=0.0,
                grounds=False,
                signal={"const": 1.0},
            )
        ]
