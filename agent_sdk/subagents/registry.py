"""``SubagentRegistry`` — the named-subagent table (mirrors ``SkillRegistry``).

Subagents are declared once and reused, like Claude Code's ``.claude/agents/*.md``. The
registry is the *resolution* surface: a fan-out work-item may name a subagent
(``{"agent": "reviewer", "input": "…"}``) and :meth:`resolve_item` expands it to the full
map-item dict the engine consumes. An item with no ``agent`` key passes through unchanged, so
raw work-items (the legacy shape) keep working — resolution is fully back-compatible.

Routing stays deterministic (invariant #4): name resolution is a dict lookup, never an LLM
judging the pipeline. The model may *name* a subagent inside the existing ``meta_control``
call; the enactor *resolves* it here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent_sdk.subagents.definition import Subagent

__all__ = ["SubagentRegistry"]


class SubagentRegistry:
    """id → :class:`Subagent`. Resolve named fan-out items to map work-items."""

    def __init__(self, agents: Sequence[Subagent] | None = None):
        self._agents: dict[str, Subagent] = {}
        for a in agents or []:
            self.register(a)

    # ── authoring ────────────────────────────────────────────────────────────
    def register(self, agent: Subagent) -> None:
        """Add (or override by name) a subagent definition."""
        if not agent.name:
            raise ValueError("cannot register a Subagent with an empty name")
        self._agents[agent.name] = agent

    def add_row(self, row: Mapping[str, object]) -> None:
        """Register a subagent from a declarative dict (sibling of ``LobeRegistry.add_row``)."""
        self.register(Subagent.from_row(row))

    @classmethod
    def from_rows(cls, rows: Sequence[Mapping[str, object]]) -> SubagentRegistry:
        reg = cls()
        for r in rows:
            reg.add_row(r)
        return reg

    # ── lookup ───────────────────────────────────────────────────────────────
    def get(self, name: str) -> Subagent | None:
        return self._agents.get(name)

    def all(self) -> list[Subagent]:
        return list(self._agents.values())

    def names(self) -> list[str]:
        return list(self._agents.keys())

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, name: object) -> bool:
        return name in self._agents

    # ── resolution (the seam the fan-out enactor calls) ──────────────────────
    def resolve_item(self, item: Mapping[str, object]) -> dict:
        """Expand a fan-out work-item, resolving an ``agent`` name against the table.

        - No ``agent`` key → return a shallow copy unchanged (raw work-item, back-compat).
        - ``agent`` names a known subagent → start from its ``to_item`` projection, then
          let inline keys on the item OVERRIDE the definition (so a call can tweak one field).
        - ``agent`` names an unknown subagent → raise ``KeyError`` (the caller surfaces a
          clear tool-error; never a silent pass).
        """
        data = dict(item)
        name = data.pop("agent", None)
        if not name:
            return data
        agent = self._agents.get(str(name))
        if agent is None:
            raise KeyError(str(name))
        base = agent.to_item(
            input=str(data.get("input") or ""),
            label=(str(data["label"]) if data.get("label") else None),
        )
        # Inline item keys win over the definition (per-call override).
        for k, v in data.items():
            if k in ("input", "label"):
                continue
            base[k] = v
        return base

    def render_catalog(self) -> str:
        """A deterministic ``name — description`` listing for the reflect step's context.

        The analogue of Claude Code surfacing agent descriptions so the model knows which
        named subagents it may delegate to. Empty registry ⇒ "" (no block)."""
        if not self._agents:
            return ""
        lines = ["Subagents you can delegate to (meta_control fan_out, item {agent, input}):"]
        for a in self._agents.values():
            desc = (a.description or "").strip().replace("\n", " ")
            lines.append(f"- {a.name}: {desc}" if desc else f"- {a.name}")
        return "\n".join(lines)
