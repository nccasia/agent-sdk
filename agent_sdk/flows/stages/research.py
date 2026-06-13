"""Research-stage definitions for the OX flow axis."""

from __future__ import annotations

from agent_sdk.flows.flow import FlowStep
from agent_sdk.flows.stages.common import Stage


class KbResearch(Stage):
    """Per-sub-question retrieval sub-agents, fanned out in parallel (loop=map).

    Reads the ``sub_questions`` planning variable the ``plan`` stage wrote to the
    turn scratchpad and runs one retrieval ReAct sub-agent per item (bounded by
    the research semaphore + ``fanout_max``). Empty list ⇒ degrades to a single
    agentic run (parity — never loses the turn). The shared-variable handoff
    (plan writes → research reads) is the concrete instance of dynamic planning.
    """

    id = "research"
    flow = "research"
    description = "research: per-sub-question retrieval sub-agents (parallel fan-out)"
    use_when = "the plan produced sub-questions that each need KB retrieval"
    how = "loop=map fan-out over sub_questions; each sub-agent retrieves + reads KB via kb.* tools"
    loop = "map"
    fanout_key = "sub_questions"
    lobes = ("research", "skill_select", "skill_active")
    # MCP kb.* contract names (RFC 0013) — the per-step tool filter matches the
    # composed runtime's spec names exactly.
    tools = ("kb.retrieve", "kb.read_chunk")


def kb_research() -> FlowStep:
    return KbResearch().spec
