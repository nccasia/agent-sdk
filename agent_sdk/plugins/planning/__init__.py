"""Planning capability — the ``TodoWrite`` tool + a plan-driven fan-out flow (docs 08 + 12).

Gives the agent Claude Code's planning loop, wired into the SDK's fan-out engine: it writes a
structured todo list with ``TodoWrite`` (each todo a *designed step* — its own ``prompt`` /
``tools`` / ``deps``); a deterministic supervisor reads the plan and picks the execution structure
(``sequential`` when steps depend on each other, ``fanout`` when they are independent); the engine
then runs **one subagent per todo** in that shape; a fan-in step aggregates the results into one
grounded answer.

Lobes contributed: ``todo_list`` (renders the plan), ``plan_supervise`` (writes the structure),
``plan_results`` (renders the subagents' results). Mount it to add the tool + lobes to any flow.
With ``flow=True`` (default) it also registers the ``plan`` flow that complex, multi-part queries
route to (``complexity_score``): plan → supervise → fanout → fanin, grounded by pinned cite/filter.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.planning.lobes import LOBE as TODO_LIST_LOBE
from agent_sdk.plugins.planning.lobes import LOBES as PLANNING_LOBES
from agent_sdk.plugins.planning.stages import planning_flow, planning_stages
from agent_sdk.plugins.planning.tool import TodoWriteToolRuntime

__all__ = ["PlanningPlugin", "TodoWriteToolRuntime", "TODO_LIST_LOBE", "PLANNING_LOBES"]


class PlanningPlugin:
    """Opt-in plan-driven fan-out: ``TodoWrite`` + planning lobes (+ an optional ``plan`` flow)."""

    name = "planning"

    def __init__(self, *, worker_tools: Sequence[str] = (), flow: bool = True):
        # ``worker_tools``: tools the plan stage may use beyond TodoWrite (each todo's own ``tools``
        # narrows the subagent that runs it). ``flow=False`` contributes only the tool + lobes
        # (compose the planning stages into your own flow).
        self._worker_tools = tuple(worker_tools)
        self._flow = flow

    def install(self, setup: AgentSetup) -> None:
        setup.add_tool_runtime(TodoWriteToolRuntime())
        for lobe in PLANNING_LOBES:
            setup.add_lobe(lobe)
        if self._flow:
            for st in planning_stages(self._worker_tools):
                setup.add_stage(st)
            setup.add_flow(planning_flow())
