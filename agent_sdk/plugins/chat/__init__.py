"""The ``chat`` plugin — a casual-conversation capability.

``PluginChat`` adds a single ``chat`` flow + stage that routes every turn straight
to a short, ungrounded reply (no retrieval/skills/tasks). Generation is the core
``synthesize`` lobe; the reply framing is whatever ``respond`` lobe the network
carries. For a persona voice, compose a lean network whose ``respond`` is your own
voice lobe (a mimic plugin providing the ``respond`` lobe on a minimal core — the
chat path then speaks in that voice). Enable it explicitly:

    agent = PreactAgent(client=…, plugins=[PluginChat()])   # plain chat path

Drop it and the chat path is gone.
"""

from __future__ import annotations

from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.chat.stages import chat_flow, chat_stage

__all__ = ["PluginChat"]


class PluginChat:
    """Casual-conversation capability (a flow + stage). A voice plugin overrides
    the ``respond`` framing on top."""

    name = "chat"
    enabled = True

    def install(self, setup: AgentSetup) -> None:
        setup.add_stage(chat_stage())
        setup.add_flow(chat_flow())
