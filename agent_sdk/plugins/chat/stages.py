"""The ``chat`` capability's stage + flow — a single casual-conversation path."""

from __future__ import annotations

from agent_sdk.flow_def import Flow, flow
from agent_sdk.stages import Stage, stage


def chat_stage() -> Stage:
    """One generating stage: route → compose a short, ungrounded reply."""
    return stage(
        "chat",
        lobes=["classify", "synthesize", "filter"],
        loop="single",
        description="Compose one short, casual reply (no retrieval).",
    )


def chat_flow() -> Flow:
    """Every casual turn goes straight to the chat stage (no sources needed). A
    baseline signal — specific intents (research/task/rag) outscore it."""
    return flow(
        "chat",
        use_when="casual conversation / chit-chat (no sources needed)",
        stages=["chat"],
        threshold=0.0,
        grounds=False,
        signal={"const": 0.5},
    )
