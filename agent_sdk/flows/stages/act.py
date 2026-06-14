"""``act`` — the canonical workhorse state of the standard flow (general agent loop).

`act` is the reasoning building block that *does the work*: one agentic ReAct loop over the agent's
full composed toolset (an empty tool filter ⇒ all composed specs), gathering and drafting. It is the
core of the generic complexity tiers — `standard` runs a single `act`; `deep` runs `act` once per
planned subject (`act → act → act`, the dynamic state plan fanning it out) before grounding.

It takes an optional **subject** (a sub-question / aspect) when the dynamic state plan instantiates it
against one piece of the work; with no subject it works on the whole turn (byte-identical to the
legacy one-shot answer). Domain-free: the SDK assumes no KB/RAG tools — a host mounts its own.
"""

from __future__ import annotations

from agent_sdk.flows.flow import FlowStep
from agent_sdk.flows.stages.common import Stage


class Act(Stage):
    """Do the work in one agentic ReAct loop over the full composed toolset."""

    id = "act"
    flow = "standard"
    description = "act: do the work in one agentic ReAct loop over the agent's full toolset"
    use_when = "the turn needs to gather/compute/answer — the workhorse state"
    how = "agentic ReAct loop; synthesize + recall + skill + task lobes; TodoWrite when planning is on"
    loop = "agentic"
    lobes = (
        "synthesize",
        "todo_list",       # live plan render when the planning plugin is mounted (research/deep)
        "skill_select",
        "skill_active",
        "memory_recall",
        "session_recall",
        "ctxvar_resolve",
        "task_state",
    )
    tools = ()  # empty ⇒ the full composed toolset (whatever the agent has)


def act() -> FlowStep:
    return Act().spec
