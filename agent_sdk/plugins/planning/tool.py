"""The ``TodoWrite`` planning tool — a reasoning tool that mimics Claude Code's training data,
extended so each todo is a **designed pipeline step**.

The agent writes/updates a structured todo list and works it in a single ReAct loop (no engine
fan-out). Reason → write → enact (docs/concepts/08): the tool **writes** the list into turn state
(``scratchpad["todos"]``); the ``todo_list`` lobe **reads + renders** it back into context each hop.
The model keeps exactly one item ``in_progress``, does it, then marks it ``completed`` and starts the
next — the TodoWrite behavior the model is trained on.

Beyond Claude Code's flat list, each todo may carry its own **step design** — a ``prompt`` (how to do
this step) and a ``tools`` subset (which tools this step uses) — so the plan is a *dynamic pipeline*:
each step is a little stage shaped to fit the work. The lobe surfaces the in-progress step's design.
"""

from __future__ import annotations

__all__ = ["TodoWriteToolRuntime", "TODOS_KEY", "render_todos"]

TODOS_KEY = "todos"
_STATUSES = ("pending", "in_progress", "completed")
_MARK = {"pending": " ", "in_progress": "~", "completed": "x"}


def render_todos(todos: list[dict]) -> str:
    """Render the todo list as a checklist block (shared by the tool result + the lobe)."""
    if not todos:
        return "(no todos yet)"
    lines = []
    for i, t in enumerate(todos, start=1):
        status = str(t.get("status") or "pending")
        mark = _MARK.get(status, " ")
        label = str(t.get("activeForm") if status == "in_progress" else t.get("content") or "")
        tools = t.get("tools") or []
        deps = t.get("deps") or []
        suffix = f"  · tools: {', '.join(tools)}" if tools else ""
        suffix += f"  · needs {', '.join(str(d) for d in deps)}" if deps else ""
        lines.append(f"{i}. [{mark}] {label}{suffix}")
    return "\n".join(lines)


class TodoWriteToolRuntime:
    """A ``ToolRuntime`` exposing the single ``TodoWrite`` tool over the turn's todo list."""

    name = "planning"

    def get_tool_specs(self) -> list[dict]:
        return [
            {
                "name": "TodoWrite",
                "description": (
                    "Create and manage a structured todo list for the current task. Use it to plan "
                    "multi-step work and track progress: send the FULL list each call (it replaces "
                    "the previous one). Mark exactly ONE item as in_progress while you work it, then "
                    "mark it completed and set the next to in_progress. Skip it for trivial "
                    "single-step requests.\n"
                    "Each todo: content (imperative, e.g. 'Compute revenue by region'), status "
                    "(pending|in_progress|completed), activeForm (present continuous shown while "
                    "in_progress). Optionally DESIGN the step: prompt (how to do this step), "
                    "tools (the tool names this step needs), and deps (the 1-based indexes of the "
                    "todos this one depends on) — so each todo is a little stage shaped to fit the "
                    "work, and the supervisor can tell independent steps (run in parallel) from "
                    "dependent ones (run in order)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "todos": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "content": {"type": "string"},
                                    "status": {"type": "string", "enum": list(_STATUSES)},
                                    "activeForm": {"type": "string"},
                                    "prompt": {"type": "string"},
                                    "tools": {"type": "array", "items": {"type": "string"}},
                                    "deps": {"type": "array", "items": {"type": "integer"}},
                                },
                                "required": ["content", "status"],
                            },
                        }
                    },
                    "required": ["todos"],
                },
            }
        ]

    async def call_tool(self, name, inp, retrieved_chunks=None, already_read=None) -> str:
        if name != "TodoWrite":
            return f"Error: unknown tool {name!r}."
        turn = self._turn()
        if turn is None:
            return "Error: no active turn — TodoWrite must be called inside a turn."
        sp = getattr(turn, "scratchpad", None)
        if sp is None:
            return "Error: no scratchpad — cannot record the todo list."
        todos: list[dict] = []
        for raw in inp.get("todos") or []:
            if not isinstance(raw, dict) or not str(raw.get("content") or "").strip():
                continue
            status = str(raw.get("status") or "pending")
            if status not in _STATUSES:
                status = "pending"
            item = {
                "content": str(raw["content"]).strip(),
                "status": status,
                "activeForm": str(raw.get("activeForm") or raw["content"]).strip(),
            }
            # Optional per-step "stage design" — preserved so a dynamic-pipeline runner (or the
            # todo_list lobe) can shape the working step to fit the todo.
            if str(raw.get("prompt") or "").strip():
                item["prompt"] = str(raw["prompt"]).strip()
            if isinstance(raw.get("tools"), list) and raw["tools"]:
                item["tools"] = [str(t) for t in raw["tools"] if str(t)]
            # deps: the indexes (or ids) of todos this one depends on. Their mere presence
            # tells the supervisor the plan is sequential (state-carry), not independent fan-out.
            if isinstance(raw.get("deps"), list) and raw["deps"]:
                item["deps"] = [d for d in raw["deps"] if d not in (None, "")]
            todos.append(item)
        if not todos:
            return "Error: TodoWrite requires 'todos': [{content, status, activeForm}]."
        sp.set(TODOS_KEY, todos)
        done = sum(1 for t in todos if t["status"] == "completed")
        return f"Todos updated ({done}/{len(todos)} done):\n{render_todos(todos)}"

    @staticmethod
    def _turn():
        from agent_sdk.engine import current_turn

        return current_turn()
