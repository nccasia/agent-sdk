"""Task capability — an OPT-IN, self-contained plugin for todo-driven task execution.

Every aspect of todo management lives here, each tunable on its own:
- ``todos.py``   — parse/manage the rail + the one ``todos`` tool (the *manage* surface).
- ``lobes.py``   — ``task_rail`` lobe: read + render the checklist into context.
- ``path.py``    — the ``task`` recognizer (when a turn is a task to drive).
- ``stages.py``  — the pipeline: ``plan → execute(map) → deliver``.

The engine stays domain-free — its generic ``loop="map"`` fans out over the work-list the
``todos`` tool publishes to turn state; this plugin supplies the *meaning*. Opt-in (not in
``default_capability_plugins``): mount ``plugins=[TaskPlugin()]`` to add it, drop it to remove
every task factor, or replace it wholesale. Self-describing · self-testable
(``tests/test_task_plugin.py``) · benchmarkable (``benchmarks/taskbench``).
"""

from __future__ import annotations

from agent_sdk.flow_def import flow
from agent_sdk.plugins.base import AgentSetup
from agent_sdk.plugins.tasks.lobes import LOBE as RAIL_LOBE
from agent_sdk.plugins.tasks.path import recognize
from agent_sdk.plugins.tasks.stages import task_stages
from agent_sdk.plugins.tasks.todos import Todo, TodoRail, TodosToolRuntime

__all__ = ["TaskPlugin", "Todo", "TodoRail", "TodosToolRuntime"]


def task_flow():
    return flow("task", use_when="accomplish a multi-step task / run a checklist to completion",
                stages=["plan", "execute", "deliver"], grounds=False, threshold=0.5, signal=recognize)


class TaskPlugin:
    """Opt-in todo-driven task execution: plan → per-todo execute → deliver; one `todos` tool."""

    name = "task"

    def lobes(self) -> list:
        return [RAIL_LOBE]

    def install(self, setup: AgentSetup) -> None:
        setup.add_lobe(RAIL_LOBE)
        for st in task_stages():
            setup.add_stage(st)
        setup.add_flow(task_flow())
        setup.add_tool(TodosToolRuntime())  # the single checklist tool (per-agent rail)
