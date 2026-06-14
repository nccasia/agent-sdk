"""Planning lobes (OY axis) — surface the plan, the chosen structure, and the results.

Three small passive lobes for the plan → supervise → execute → fanin flow:

- ``todo_list`` renders the plan the ``TodoWrite`` tool published, each hop (the *enact* half
  of reason → write → enact) — the model sees its plan + the in-progress step's design.
- ``plan_supervise`` is the **supervisor**: it reads the plan and writes the execution
  ``plan_structure`` to turn state — ``"fanout"`` when the steps are independent (parallel,
  isolated subagent per todo), ``"sequential"`` when any todo declares ``deps`` (state-carry,
  in order). A pure function of the plan's deps graph (invariant #4), it is the reason → write
  step the engine's map dispatch enacts.
- ``plan_results`` renders every subagent's result (``scratchpad["todos_results"]``) for the
  fan-in step to aggregate.

Each contributes nothing when its turn state is absent (harmless wherever listed).
"""

from __future__ import annotations

from agent_sdk.contracts.turn import PromptContribution, TurnContext
from agent_sdk.lobes.runtime import Lobe
from agent_sdk.network.activation import LAYER_COGNITION, LAYER_MEMORY
from agent_sdk.plugins.planning.tool import TODOS_KEY, render_todos

__all__ = ["TodoListLobe", "PlanSuperviseLobe", "PlanResultsLobe", "LOBE", "LOBES"]

RESULTS_KEY = TODOS_KEY + "_results"
STRUCTURE_KEY = "plan_structure"
# ≥ this many independent steps ⇒ fan out (parallel isolated subagents); fewer ⇒ work inline.
_FANOUT_MIN = 3


def _todos(ctx: TurnContext) -> list[dict]:
    sp = getattr(ctx, "scratchpad", None)
    return [t for t in sp.as_list(TODOS_KEY) if isinstance(t, dict)] if sp else []


class TodoListLobe(Lobe):
    """Render the agent's current todo list (the plan it manages via TodoWrite)."""

    id = "todo_list"
    name = "Todo List"
    description = "Renders the agent's TodoWrite todo list (its working plan) into context."
    use_when = "the agent is working a multi-step task it planned with TodoWrite"
    how = "reads the todo list the TodoWrite tool published to turn state and renders it"
    layer = LAYER_MEMORY
    behavior = "recall"
    prior = 1.0  # active wherever a stage lists it; prompt() is empty without a list

    def prompt(self, ctx: TurnContext) -> list[PromptContribution]:
        todos = _todos(ctx)
        if not todos:
            return []
        done = sum(1 for t in todos if t.get("status") == "completed")
        block = (
            f"## Todos ({done}/{len(todos)} done)\n"
            "Your working plan. Keep exactly one item in_progress; finish it, mark it completed via "
            "TodoWrite, then start the next. When all are completed, write the final answer.\n"
            + render_todos(todos)
        )
        # Surface the in-progress step's own design (its prompt) so the agent works it as planned.
        cur = next((t for t in todos if t.get("status") == "in_progress"), None)
        if cur and cur.get("prompt"):
            block += f"\n\n### Current step: {cur.get('content')}\n{cur['prompt']}"
        return [PromptContribution(block, stability="volatile", source=self.id)]


class PlanSuperviseLobe(Lobe):
    """Supervisor: read the plan, choose + record the execution structure (reason → write).

    The structure is a deterministic function of the plan's shape — whichever it picks, every
    planned piece is solved (by a subagent, or by the main agent itself) and fed to fan-in:

    - ``"sequential"`` — any todo declares ``deps`` ⇒ one subagent per todo, in order, each seeing
      prior results (state-carry).
    - ``"fanout"`` — several independent pieces (``≥ _FANOUT_MIN`` todos, or any step the model
      designed with its own ``prompt``/``tools``) ⇒ one parallel, isolated subagent per todo. Wide
      independent work is where isolation + parallelism pay off.
    - ``"inline"`` — a small independent plan (2 plain steps) ⇒ the main agent works the list
      itself in the execute stage (no subagent spawn — the Claude-Code "work the plan" loop).

    Writes ``scratchpad["plan_structure"]``; the engine's ``loop="map"`` dispatch enacts it.
    Metacognition may overwrite the key to override (replan / force a structure)."""

    id = "plan_supervise"
    name = "Plan Supervisor"
    description = "Reads the plan and picks the execution structure (inline / fanout / sequential)."
    use_when = "a plan has been written and the engine is about to run it"
    how = "inspects the plan's deps graph + per-step design and writes scratchpad['plan_structure']"
    layer = LAYER_COGNITION
    behavior = "monitor"
    prior = 1.0

    def prompt(self, ctx: TurnContext) -> list[PromptContribution]:
        todos = _todos(ctx)
        sp = getattr(ctx, "scratchpad", None)
        if sp is None or len(todos) < 2:
            return []  # a 0/1-step plan runs inline as a single agentic loop (engine degrade)
        designed = any(t.get("prompt") or t.get("tools") for t in todos)
        if any(t.get("deps") for t in todos):
            structure = "sequential"
        elif len(todos) >= _FANOUT_MIN or designed:
            # several independent pieces (or model-designed steps) → one isolated subagent each
            structure = "fanout"
        else:
            structure = "inline"  # a small plain plan → the main agent works the list itself
        sp.set(STRUCTURE_KEY, structure)
        shape = {
            "sequential": "dependent — run in order, each subagent seeing prior results",
            "fanout": "independent + designed — run as parallel subagents, one per todo",
            "inline": "independent + light — work the list yourself in this stage (no subagents)",
        }[structure]
        note = f"## Execution plan\n{len(todos)} steps are {shape} ({structure})."
        return [PromptContribution(note, stability="volatile", source=self.id)]


class PlanResultsLobe(Lobe):
    """Render every subagent's result for the fan-in step to aggregate into one answer."""

    id = "plan_results"
    name = "Plan Results"
    description = "Renders the per-todo subagent results (scratchpad['todos_results'])."
    use_when = "the fan-in step is aggregating the subagents' work into one answer"
    how = "reads scratchpad['todos_results'] and renders each subagent's result"
    layer = LAYER_MEMORY
    behavior = "recall"
    prior = 1.0

    def prompt(self, ctx: TurnContext) -> list[PromptContribution]:
        sp = getattr(ctx, "scratchpad", None)
        # Skip scratchpad cap markers ({"_elided"}/{"_truncated"}) — only real result rows.
        results = (
            [r for r in sp.as_list(RESULTS_KEY) if isinstance(r, dict) and r.get("status")]
            if sp else []
        )
        if not results:
            return []
        lines = []
        for r in results:
            label = str(r.get("label") or "step")
            if r.get("status") == "failed":
                lines.append(f"### {label} (failed)\n{r.get('error') or 'no result'}")
            else:
                lines.append(f"### {label}\n{r.get('result') or ''}")
        block = (
            "## Subagent results\n"
            "Each step ran as its own subagent; their results are below. Aggregate them into one "
            "combined answer that covers every part — do not re-run the work.\n\n"
            + "\n\n".join(lines)
        )
        return [PromptContribution(block, stability="volatile", source=self.id)]


LOBE = TodoListLobe()
LOBES = [LOBE, PlanSuperviseLobe(), PlanResultsLobe()]
