"""The ``chat()`` preset — a lean casual-conversation network.

A chit-chat / persona agent is not a RAG/skills/tasks assistant, so ``.chat()``
ships ``classify → synthesize`` + safety only, with no retrieval/skills/tools/
tasks/memory lobes.
"""

from __future__ import annotations

from agent_sdk import PreactAgent
from agent_sdk.clients.fake import FakeClient
from agent_sdk.preact.defaults import Flows, Lobes, Stages


def _ids(lobes) -> set[str]:
    return {getattr(lobe, "id", "") for lobe in lobes}


def test_chat_lobes_are_lean():
    ids = _ids(Lobes.chat())
    assert ids == {"classify", "synthesize", "filter"}
    # none of the RAG / skills / tools / tasks / memory machinery
    for noise in ("plan", "research", "cite", "clarify", "skill_select",
                  "memory_recall", "session_recall", "tool_select"):
        assert noise not in ids


def test_chat_flow_is_single_and_ungrounded():
    flows = Flows.chat()
    assert len(flows) == 1
    f = flows[0]
    assert f.id == "chat"
    assert list(f.stages) == ["synthesize"]
    assert f.grounds is False


def test_chat_stage_is_a_single_generator():
    stages = Stages.chat()
    assert len(stages) == 1
    assert stages[0].id == "synthesize"


async def test_chat_agent_runs_a_turn():
    agent = PreactAgent(
        client=FakeClient(default="ờ chào"),
        instructions="Bạn là một người bạn.",
        lobes=Lobes.chat(),
        stages=Stages.chat(),
        flows=Flows.chat(),
        universal_memory=False,
        auto_establish=False,
    )
    result = await agent.query("ê có gì hot không")
    assert result.text  # the lean chat network still produces a reply
