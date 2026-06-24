"""The ``chat`` plugin — a casual-conversation capability."""

from __future__ import annotations

from agent_sdk import PreactAgent
from agent_sdk.clients.fake import FakeClient
from agent_sdk.plugins import PluginChat
from agent_sdk.plugins.base import AgentSetup


def test_chat_plugin_adds_a_chat_flow_and_stage():
    setup = AgentSetup()
    PluginChat().install(setup)
    assert [s.id for s in setup.stages] == ["chat"]
    assert [f.id for f in setup.flows] == ["chat"]
    flow = setup.flows[0]
    assert flow.grounds is False
    assert list(flow.stages) == ["chat"]


async def test_chat_plugin_runs_a_casual_turn():
    agent = PreactAgent(
        client=FakeClient(default="ờ chào"),
        instructions="Bạn là một người bạn.",
        plugins=[PluginChat()],
        universal_memory=False,
        auto_establish=False,
    )
    result = await agent.query("ê có gì hot không")
    assert result.text  # the chat path produced a reply
