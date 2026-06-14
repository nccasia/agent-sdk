"""``Subagent`` — a named, reusable scoped worker (the map-item dict, promoted).

A subagent is a scoped sub-execution defined by ``(prompt, tools, lobes, model, budget)``
that returns a compressed result, never its raw context — the unit doc 12
(*Subagent Fan-out*) calls "to build". It is the *typed, named* form of the ad-hoc work-item
dict the engine's generic ``loop="map"`` already consumes (``Engine._map_stage`` reads
``label`` / ``id`` / ``input`` / ``system_prompt`` / ``lobes`` / ``tools`` / ``model`` /
``max_tokens`` / ``hops``). :meth:`Subagent.to_item` is therefore a trivial projection — the
engine stays unchanged; a subagent is just a reusable way to author one of its work-items.

This mirrors Claude Code's ``.claude/agents/*.md`` ``AgentDefinition``: ``name`` +
``description`` (when to delegate) + ``tools`` (an allowlist) + ``model`` + ``prompt``.
Define once (in code via :class:`~agent_sdk.subagents.registry.SubagentRegistry`, or as a
markdown file via :func:`~agent_sdk.subagents.loader.load_agents_dir`), invoke many.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = ["Subagent"]


@dataclass(frozen=True)
class Subagent:
    """A reusable, scoped worker definition. Projects to one ``map`` work-item.

    Field names are chosen to match what ``Engine._map_stage`` reads so ``to_item`` is a
    direct projection — no kernel change. An empty ``tools`` / ``lobes`` means *inherit the
    fan-out stage's* belt (the engine falls back to ``stage.tools`` / ``stage.lobes``).
    """

    name: str  # stable id (== Claude Code agent name)
    description: str = ""  # "when to delegate" — surfaced to the reflect step
    instructions: str = ""  # the worker's system prompt (→ item "system_prompt")
    tools: tuple[str, ...] = ()  # restricted allowlist (empty ⇒ inherit stage)
    lobes: tuple[str, ...] = ()  # context belt (empty ⇒ inherit stage)
    model: str | None = None
    max_tokens: int | None = None
    hops: int | None = None

    def __post_init__(self) -> None:
        if not str(self.name).strip():
            raise ValueError("Subagent requires a non-empty name")
        # Normalize sequences passed as lists into tuples (frozen dataclass).
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "lobes", tuple(self.lobes))

    def to_item(self, *, input: str, label: str | None = None) -> dict:
        """Project to the map work-item dict ``Engine._map_stage`` consumes.

        Only set keys are emitted, so unset fields fall through to the stage defaults
        exactly as a hand-written work-item would.
        """
        item: dict = {"id": self.name, "label": label or self.name, "input": input}
        if self.instructions:
            item["system_prompt"] = self.instructions
        if self.tools:
            item["tools"] = list(self.tools)
        if self.lobes:
            item["lobes"] = list(self.lobes)
        if self.model:
            item["model"] = self.model
        if self.max_tokens:
            item["max_tokens"] = self.max_tokens
        if self.hops:
            item["hops"] = self.hops
        return item

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> Subagent:
        """Build from a declarative dict (registry row / parsed frontmatter)."""

        def _seq(v: object) -> tuple[str, ...]:
            if v is None:
                return ()
            if isinstance(v, str):
                return tuple(p.strip() for p in v.split(",") if p.strip())
            if isinstance(v, Sequence):
                return tuple(str(p).strip() for p in v if str(p).strip())
            return ()

        def _int(v: object) -> int | None:
            if v is None or v == "":
                return None
            return int(v)  # type: ignore[arg-type]

        return cls(
            name=str(row.get("name") or "").strip(),
            description=str(row.get("description") or ""),
            instructions=str(row.get("instructions") or row.get("prompt") or ""),
            tools=_seq(row.get("tools")),
            lobes=_seq(row.get("lobes")),
            model=(str(row["model"]) if row.get("model") else None),
            max_tokens=_int(row.get("max_tokens")),
            hops=_int(row.get("hops")),
        )
