"""Façade ``Skill`` — procedural knowledge, progressively disclosed, ``Activable``.

A Skill carries the uniform Activable surface: ``when`` is its ``use_when`` and
its ``signal`` is what the skill-select step uses to decide whether to surface it
this turn. It compiles to the ported :class:`SkillPack` the runtime consumes.

    Skill(
        id="code_review",
        when="reviewing pull requests",
        instructions="Check logic, tests, security…",
        tools=["search"],
        disclosure="on_demand",        # "eager" (inline) | "on_demand" (model calls skill.read)
        files={"GUIDE.md": "## Deep checklist …"},
    )
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agent_sdk.signals import compile_signal
from agent_sdk.skills.packs import SkillPack

__all__ = ["Skill"]


class Skill:
    def __init__(
        self,
        id: str,
        *,
        when: str = "",
        instructions: str = "",
        tools: Sequence[str] = (),
        disclosure: str = "on_demand",
        files: Mapping[str, str] | None = None,
        name: str = "",
        description: str = "",
        stages: Sequence[str] = (),
        checklist: Sequence[Mapping[str, Any]] = (),
        context_vars: Sequence[Mapping[str, Any]] = (),
        signal: Callable[[dict], float] | dict | float | None = None,
        source_dir: str | None = None,
    ):
        if disclosure not in ("eager", "on_demand"):
            raise ValueError("disclosure must be 'eager' or 'on_demand'")
        self.id = id
        self.use_when = when
        self.instructions = instructions
        self.tools = tuple(tools)
        self.disclosure = disclosure
        self.files = dict(files or {})
        self.name = name or id
        self.description = description or when
        self.stages = tuple(stages)
        # Declarative wizard steps + skill-scoped workspace state (checklist/todos/
        # notes/var). Carried through to the SkillPack the skill_active lobe drives.
        self.checklist = tuple(dict(c) for c in checklist)
        self.context_vars = tuple(dict(v) for v in context_vars)
        self.source_dir = source_dir
        if signal is None:
            self._signal_fn: Callable[[dict], float] | None = None
        elif callable(signal):
            self._signal_fn = signal
        else:
            self._signal_fn = compile_signal(signal)

    def signal(self, ctx: dict) -> float:
        if self._signal_fn is not None:
            return float(self._signal_fn(ctx))
        return 0.0

    def to_pack(self) -> SkillPack:
        return SkillPack(
            id=self.id,
            name=self.name,
            description=self.description,
            stages=self.stages,
            instructions=self.instructions,
            required_tools=self.tools,
            injection=self.disclosure,
            files=self.files,
            checklist=self.checklist,
            context_vars=self.context_vars,
            source_dir=self.source_dir,
        )

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"Skill(id={self.id!r}, disclosure={self.disclosure!r})"
