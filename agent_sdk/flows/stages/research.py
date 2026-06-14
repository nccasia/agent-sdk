"""Research-stage definition for the OX flow axis — GENERAL (not KB-specific).

A single ReAct investigation step: the model investigates the question in one agentic loop over
**whatever tools the agent has** (an empty tool filter ⇒ the full composed toolset), planning its
sub-steps with ``TodoWrite`` when the planning capability is mounted (the ``todo_list`` lobe renders
the live plan). The flow then grounds the draft (``cite`` → ``filter``).

The SDK stays domain-free: it does NOT assume KB/RAG tools — a project (e.g. agent-core) mounts its
own ``kb.*`` tools (or a KB-specific research flow) on top. Loop model: every stage is ``agentic``
or ``single``.
"""

from __future__ import annotations

from agent_sdk.flows.flow import FlowStep
from agent_sdk.flows.stages.common import Stage


class ResearchInvestigate(Stage):
    """Investigate a complex question in one ReAct loop over the agent's full toolset (general)."""

    id = "investigate"
    flow = "research"
    description = "research: investigate the question in one ReAct loop over the agent's tools"
    use_when = "a complex question that needs multi-step investigation"
    how = "agentic ReAct loop over the full composed toolset; plan sub-steps with TodoWrite"
    loop = "agentic"
    lobes = (
        "synthesize",
        "todo_list",
        "skill_select",
        "skill_active",
        "memory_recall",
        "session_recall",
    )
    # Empty tool filter ⇒ the full composed toolset (whatever the agent has). No KB/RAG assumption —
    # a project mounts its own world tools; ``TodoWrite`` is honored when the planning plugin is on.
    tools = ()


def research_investigate() -> FlowStep:
    return ResearchInvestigate().spec
