"""Planning stages (OX axis) + the `plan` flow — plan → supervise → fanout → fanin.

A complex query routes here (``complexity_score``). The pipeline is the plan-driven dynamic
pipeline (docs/concepts/08 + 12):

1. ``plan`` — the model lays out a todo list with ``TodoWrite`` (one todo per part; each may
   carry its own ``prompt`` / ``tools`` / ``deps``). PLAN ONLY — it does not do the work.
2. ``supervise`` — the ``plan_supervise`` lobe reads the plan and writes the execution
   ``plan_structure`` (sequential when any todo has ``deps``, else fanout). Deterministic; no LLM.
3. ``execute`` — ``loop="map"`` over the todos: one subagent per todo, scoped by that todo's
   design, run in the supervisor-chosen shape (parallel + isolated, or sequential state-carry).
4. ``fanin`` — aggregate every subagent's result into one answer; ``cite`` → ``filter`` ground it.

The plan IS the spawn list — no separate ``Subagent`` tool. Every stage is a self-describing,
independently tunable unit (its own lobe slice / loop / tools / prompt).
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_sdk.flow_def import flow
from agent_sdk.plugins.planning.path import recognize
from agent_sdk.stages import stage

__all__ = ["planning_stages", "planning_flow"]

_PLAN_PROMPT = (
    "Plan this task into a TodoWrite list — one todo per part — then stop; do not start the work "
    "yet. Each todo has: content (imperative), status 'pending', activeForm. Design a substantial, "
    "independent step as its own worker — give it a prompt (how to do it) and the tools it needs "
    "(it runs as a subagent); leave a light step plain (content only). Set deps to the 1-based "
    "indexes a step waits on; omit deps for independent steps (they run in parallel).\n"
    'Example todo: {"content": "Benchmark both parsers", "status": "pending", '
    '"activeForm": "Benchmarking parsers", "prompt": "Run bench X on parsers A and B, report p95", '
    '"tools": ["Bash"], "deps": [1]}\n'
    "If the task is really a single step, answer it directly instead of planning."
)
_FANIN_PROMPT = (
    "The planned work is COMPLETE — each step's result is above (either in 'Subagent results', if "
    "the steps ran as subagents, or earlier in this conversation, if you worked them inline). Do "
    "NOT re-run or re-plan any work. Aggregate the results into ONE combined answer that covers "
    "every part of the original request, reading the concrete findings directly from those results."
)


def planning_stages(worker_tools: Sequence[str] = ()) -> list:
    plan_tools = ["TodoWrite", *[t for t in worker_tools if t and t != "TodoWrite"]]
    return [
        stage(
            "plan",
            lobes=["todo_list", "synthesize", "skill_select", "skill_active", "memory_recall"],
            loop="agentic",
            tools=plan_tools,
            hops=10,
            description="Plan the task into a TodoWrite todo list (one designed step per part).",
            system_prompt=_PLAN_PROMPT,
        ),
        # Deterministic supervisor: the plan_supervise lobe reads the plan and writes
        # scratchpad['plan_structure']; no LLM call (loop='none').
        stage(
            "supervise",
            lobes=["plan_supervise"],
            loop="none",
            description="Pick the execution structure (sequential vs fanout) from the plan.",
        ),
        # Plan-driven fan-out: one subagent per todo, scoped by the todo's prompt/tools, in the
        # supervisor-chosen shape. tools=() ⇒ each subagent sees the full toolset; the per-todo
        # 'tools' narrows it. The engine reads plan_structure to set parallel/isolated per turn.
        stage(
            "execute",
            lobes=["todo_list", "synthesize"],
            loop="map",
            fanout_key="todos",
            hops=12,
            description="Run one subagent per todo (the plan-driven fan-out).",
        ),
        stage(
            "fanin",
            lobes=["plan_results", "synthesize"],
            loop="agentic",
            hops=8,
            description="Aggregate every subagent's result into one combined answer.",
            system_prompt=_FANIN_PROMPT,
        ),
    ]


def planning_flow():
    """The opt-in ``plan`` flow: plan → supervise → fanout → fanin, grounded, on complex queries."""
    return flow(
        "plan",
        use_when="the task has several parts worth planning into a todo list and fanning out",
        stages=["plan", "supervise", "execute", "fanin"],
        grounds=True,
        threshold=0.5,
        signal=recognize,
    )
