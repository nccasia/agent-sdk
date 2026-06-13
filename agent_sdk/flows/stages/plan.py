"""Plan-stage definitions for the OX flow axis."""

from __future__ import annotations

from agent_sdk.flows.flow import FlowStep
from agent_sdk.flows.stages.common import Stage


class ResearchPlan(Stage):
    """Decompose a complex question into research aspects (sub-questions).

    Writes ``sub_questions`` to the turn scratchpad — the first shared planning
    variable downstream stages consume (the ``research`` stage fans out over it).
    The planner-as-a-stage in miniature: a stage whose output is a context
    variable the rest of the plan reads.
    """

    id = "plan"
    flow = "research"
    description = "research: decompose the question into research aspects"
    use_when = "a complex question that must be decomposed before retrieval"
    how = "one LLM pass writing sub_questions to the turn scratchpad for the research fan-out"
    loop = "single"
    lobes = ("plan", "skill_select", "skill_active", "memory_recall", "session_recall", "task_state")


def research_plan() -> FlowStep:
    return ResearchPlan().spec
