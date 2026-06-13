"""Task lobes (OY axis) — read + render the todo rail into context.

``task_rail`` surfaces the checklist the ``todos`` tool published to turn state: the
ordered steps and, once the per-todo driver has run them, which are done. It is the
*read/render* half of the capability (the tool is the *manage* half); tunable on its
own — prompt wording, weight, which stages consult it — exactly like any lobe.
"""

from __future__ import annotations

from agent_sdk.contracts.turn import PromptContribution, TurnContext
from agent_sdk.lobes.runtime import Lobe
from agent_sdk.network.activation import LAYER_MEMORY

__all__ = ["TaskRailLobe", "LOBE"]

_FANOUT_KEY = "todos"  # the scratchpad key the todos tool / engine map share


class TaskRailLobe(Lobe):
    """Render the task's checklist (the rail) — open steps + their dependencies, with
    completed steps marked once the per-todo driver records results. Always-on where a
    task stage lists it; contributes nothing when there is no rail (harmless elsewhere)."""

    id = "task_rail"
    name = "Task Rail"
    description = "Renders the task's todo checklist (the rail) into context."
    use_when = "a task checklist has been planned this turn"
    how = "reads the rail the todos tool published to turn state and renders it as a checklist"
    layer = LAYER_MEMORY
    behavior = "recall"
    prior = 1.0  # active wherever a task stage consults it; prompt() is empty without a rail

    def prompt(self, ctx: TurnContext) -> list[PromptContribution]:
        sp = getattr(ctx, "scratchpad", None)
        if sp is None:
            return []
        items = sp.as_list(_FANOUT_KEY)
        if not items:
            return []
        done = {r.get("label") for r in sp.as_list(_FANOUT_KEY + "_results") if isinstance(r, dict)}
        lines = []
        for it in items:
            if not isinstance(it, dict):
                continue
            label = it.get("id") or it.get("label") or "?"
            mark = "x" if label in done else " "
            deps = f" (needs {', '.join(it['deps'])})" if it.get("deps") else ""
            lines.append(f"- [{mark}] {label}: {it.get('input', '')}{deps}")
        block = "## Task checklist (the rail)\n" + "\n".join(lines)
        return [PromptContribution(block, stability="volatile", source=self.id)]


LOBE = TaskRailLobe()
