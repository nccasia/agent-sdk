"""The todo rail + the single ``todos`` tool.

``TodoRail`` is the durable execution rail: an ordered list of ``Todo``s with
dependency tracking. A ``Todo`` is a rail step that can stay a bare title (the
default executor handles it) or carry an optional ``spec``
(``tools``/``lobes``/``system_prompt``/``model``/``max_tokens``/``loop``) — the
per-todo optimization surface the engine's per-todo driver (Phase 2) consumes.

``TodosToolRuntime`` exposes ONE ``todos`` tool (action-dispatched: add /
add_many / list / done / block / request_human), replacing the former 4 separate
tools — the same "one tool, many actions" shape as the ``memory`` tool
(``RecallToolRuntime``), so the prompt's tool menu stays lean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["Todo", "TodoRail", "TodosToolRuntime"]

OPEN_STATUSES = ("todo", "doing", "blocked")
_DONE = ("done", "skipped")
_ACTIONS = ("add", "add_many", "list", "done", "block", "request_human")
# Per-todo execution spec keys (consumed by the engine's per-todo driver).
_SPEC_KEYS = ("tools", "lobes", "system_prompt", "model", "max_tokens", "loop")


@dataclass
class Todo:
    id: str
    title: str
    status: str = "todo"  # todo | doing | blocked | done | failed | skipped
    deps: tuple[str, ...] = ()
    result: str = ""
    error: str = ""
    tool_hint: str = ""
    # Optional per-step optimization: when present, the per-todo driver runs this
    # todo with its own prompt/tools/lobes/model instead of the default executor.
    spec: dict[str, Any] | None = None

    def to_json(self) -> dict:
        return {"id": self.id, "title": self.title, "status": self.status,
                "deps": list(self.deps), "result": self.result, "error": self.error,
                "tool_hint": self.tool_hint, "spec": self.spec}


@dataclass
class TodoRail:
    """The ordered todo rail (the execution rail). Mutated by the ``todos`` tool."""

    todos: list[Todo] = field(default_factory=list)

    # ── build ──────────────────────────────────────────────────────────────
    def add(self, title: str, *, deps=(), tool_hint: str = "", spec: dict | None = None) -> Todo:
        t = Todo(id=f"t{len(self.todos)}", title=title, deps=tuple(deps or ()),
                 tool_hint=tool_hint, spec=(spec or None))
        self.todos.append(t)
        return t

    # ── query ──────────────────────────────────────────────────────────────
    def by_id(self, todo_id: str) -> Todo | None:
        return next((t for t in self.todos if t.id == todo_id), None)

    def open(self) -> list[Todo]:
        return [t for t in self.todos if t.status in OPEN_STATUSES]

    def _deps_unmet(self, t: Todo) -> list[str]:
        return [d for d in t.deps if (self.by_id(d) or Todo("", "")).status not in _DONE]

    def ready(self) -> list[Todo]:
        """Open todos whose dependencies are all done/skipped (runnable now)."""
        return [t for t in self.open() if not self._deps_unmet(t)]

    def is_complete(self) -> bool:
        return bool(self.todos) and not self.open()

    def topo_order(self) -> list[Todo]:
        """Todos in dependency order (a todo follows its deps). Cyclic/dangling go last."""
        ordered: list[Todo] = []
        placed: set[str] = set()
        remaining = list(self.todos)
        while remaining:
            progressed = False
            for t in list(remaining):
                if all(d in placed for d in t.deps):
                    ordered.append(t)
                    placed.add(t.id)
                    remaining.remove(t)
                    progressed = True
            if not progressed:
                break
        return ordered + remaining

    def as_items(self) -> list[dict]:
        """Render the rail as the engine's generic map work-list (dependency order).
        Each item carries the todo's optional ``spec`` overrides for its sub-execution."""
        return [
            {"label": t.id, "id": t.id, "input": t.title, **(t.spec or {})}
            for t in self.topo_order()
        ]

    # ── advance ──────────────────────────────────────────────────────────────
    def _target(self, todo_id: str | None) -> Todo | None:
        if todo_id:
            return self.by_id(todo_id)
        # default to the first ready todo, else the first open one
        return next(iter(self.ready()), None) or next(iter(self.open()), None)

    def to_json(self) -> list[dict]:
        return [t.to_json() for t in self.todos]


def _spec_from(inp: dict) -> dict | None:
    spec = {k: inp[k] for k in _SPEC_KEYS if inp.get(k) not in (None, "", [])}
    return spec or None


class TodosToolRuntime:
    """A ``ToolRuntime`` exposing the single ``todos`` tool over a ``TodoRail``."""

    def __init__(self, rail: TodoRail | None = None, *, fanout_key: str = "todos"):
        self.rail = rail if rail is not None else TodoRail()
        self.fanout_key = fanout_key  # the scratchpad key the engine's map stage fans out over
        self.events: list[dict] = []  # {action, id?} — telemetry
        self.human_asks: list[str] = []

    def _sync(self) -> None:
        """Publish the rail to the turn scratchpad as the generic map work-list. The engine
        owns the turn; this tool opts into it via ``current_turn()`` — no engine task logic."""
        from agent_sdk.engine import current_turn
        turn = current_turn()
        sp = getattr(turn, "scratchpad", None)
        if sp is not None:
            sp.set(self.fanout_key, self.rail.as_items())

    def get_tool_specs(self) -> list[dict]:
        return [{
            "name": "todos",
            "description": (
                "Manage the task's checklist (the execution rail). One tool, choose `action`:\n"
                "- add: append one step {title, deps?: [ids], tool_hint?}\n"
                "- add_many: append several at once {steps: [{title, deps?, tool_hint?}]}\n"
                "- list: show the current checklist (open items + statuses)\n"
                "- done: mark a step finished {id?, result} (id defaults to the next open step)\n"
                "- block: mark a step blocked {id?, reason}\n"
                "- request_human: escalate a blocked step {id?, question}\n"
                "Build the rail first (add/add_many), then advance it (done/block) until empty."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(_ACTIONS)},
                    "title": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "object"}},
                    "deps": {"type": "array", "items": {"type": "string"}},
                    "tool_hint": {"type": "string"},
                    "id": {"type": "string"},
                    "result": {"type": "string"},
                    "reason": {"type": "string"},
                    "question": {"type": "string"},
                },
                "required": ["action"],
            },
        }]

    async def call_tool(self, name: str, inp: dict, retrieved_chunks=None, already_read=None) -> str:
        if name != "todos":
            return f"Error: unknown tool {name!r}."
        action = str(inp.get("action") or "").lower()
        if action == "add":
            return self._add(inp)
        if action == "add_many":
            return self._add_many(inp)
        if action == "list":
            return self._list()
        if action in ("done", "block", "request_human"):
            return self._advance(action, inp)
        return f"Error: unknown action {action!r}. Use one of {', '.join(_ACTIONS)}."

    # ── handlers ──────────────────────────────────────────────────────────────
    def _add(self, inp: dict) -> str:
        if not inp.get("title"):
            return "Error: add requires a 'title'."
        t = self.rail.add(inp["title"], deps=inp.get("deps") or (),
                          tool_hint=inp.get("tool_hint", ""), spec=_spec_from(inp))
        self.events.append({"action": "add", "id": t.id})
        self._sync()
        return f"Added {t.id}: {t.title}.\n{self._checklist()}"

    def _add_many(self, inp: dict) -> str:
        ids = []
        for s in inp.get("steps") or []:
            if isinstance(s, dict) and s.get("title"):
                t = self.rail.add(s["title"], deps=s.get("deps") or (), tool_hint=s.get("tool_hint", ""),
                                  spec=_spec_from(s))
                ids.append(t.id)
        if not ids:
            return "Error: add_many requires 'steps': [{title, deps?}]."
        self.events.append({"action": "add_many", "n": len(ids)})
        self._sync()
        return f"Added {len(ids)} steps ({', '.join(ids)}).\n{self._checklist()}"

    def _list(self) -> str:
        return self._checklist()

    def _advance(self, action: str, inp: dict) -> str:
        t = self.rail._target(inp.get("id"))
        if t is None:
            return "Error: no such todo (and no open step). Call todos action=list."
        if action == "done":
            unmet = self.rail._deps_unmet(t)
            if unmet:
                return f"Error: {t.id} needs {', '.join(unmet)} done first (dependency order)."
            t.status, t.result = "done", inp.get("result", t.result)
        elif action == "block":
            t.status, t.error = "blocked", inp.get("reason", "")
        else:  # request_human
            t.status = "blocked"
            q = inp.get("question", "")
            self.human_asks.append(q)
            self.events.append({"action": "request_human", "id": t.id})
            return f"Escalated {t.id} to a human: {q!r}. (Paused.)"
        self.events.append({"action": action, "id": t.id})
        self._sync()
        if self.rail.is_complete():
            return f"{t.id} → {t.status}. All steps done — checklist complete."
        return f"{t.id} → {t.status}.\n{self._checklist()}"

    def _checklist(self) -> str:
        items = self.rail.open()
        if not items:
            return "Checklist complete — no open steps." if self.rail.todos else "No steps yet."
        ready = {t.id for t in self.rail.ready()}
        lines = [
            f"  {t.id}: [{t.status}] {t.title}"
            + (f" (needs {', '.join(self.rail._deps_unmet(t))})" if self.rail._deps_unmet(t)
               else (" ← do this next" if t.id in ready else ""))
            for t in items
        ]
        return "Checklist:\n" + "\n".join(lines)
