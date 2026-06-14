"""Compile a skill into a budget-bounded "surface" — the core, plus chunk refs.

A real SOP bundle is too big to inline on every activation. The compiler turns it
into a :class:`CompiledSkill`: a dense ``surface`` (the must-know core, within a
token ``budget``) that REFERENCES deeper ``chunks`` by id — so ``ActivateSkill``
returns the compact core and the model pulls a chunk back with ``skill.read`` only
when a step needs it.

Domain-pure: no I/O, no caching, no client construction. ``compile_skill`` takes an
injected ``llm`` (the SDK ``LlmCall``); ``llm=None`` (or any error) degrades to a
deterministic surface (description + chunk index), so it never fails a turn. The
lazy compile-on-activate + caching lives in ``agent_sdk.skills.cache`` /
``SkillToolRuntime``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from agent_sdk.skills.parser import est_tokens, split_sections

DEFAULT_BUDGET = 600

_COMPILE_PROMPT = (
    "You are compiling a Standard Operating Procedure into a COMPACT surface for an AI "
    "agent that will follow it. Write the must-know core — the steps and decisions to apply "
    "every time this skill is used — in AT MOST {budget} tokens. Do NOT inline reference "
    "detail; instead point to chunk ids in square brackets like [file#section] where the "
    "agent can skill.read the detail when a step needs it. Output only the surface text."
)


@dataclass(frozen=True)
class SkillChunk:
    """One addressable piece of a skill bundle (a section of SKILL.md or a file)."""

    id: str  # "<file>#<section_id>" — directly skill.read-able
    source_file: str
    heading: str
    tokens: int
    gist: str

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "source_file": self.source_file,
            "heading": self.heading,
            "tokens": self.tokens,
            "gist": self.gist,
        }


@dataclass(frozen=True)
class CompiledSkill:
    """A skill's compiled surface + the chunk index it references."""

    slug: str
    content_hash: str
    budget_tokens: int
    surface: str
    chunks: tuple[SkillChunk, ...]
    built_by: str  # "llm" | "deterministic"

    def to_json(self) -> dict:
        return {
            "slug": self.slug,
            "content_hash": self.content_hash,
            "budget_tokens": self.budget_tokens,
            "surface": self.surface,
            "chunks": [c.to_json() for c in self.chunks],
            "built_by": self.built_by,
        }

    @classmethod
    def from_json(cls, d: dict) -> CompiledSkill:
        return cls(
            slug=str(d.get("slug", "")),
            content_hash=str(d.get("content_hash", "")),
            budget_tokens=int(d.get("budget_tokens", DEFAULT_BUDGET)),
            surface=str(d.get("surface", "")),
            chunks=tuple(
                SkillChunk(
                    **{k: c.get(k) for k in ("id", "source_file", "heading", "tokens", "gist")}
                )
                for c in (d.get("chunks") or [])
            ),
            built_by=str(d.get("built_by", "deterministic")),
        )


def content_hash(pack: Any) -> str:
    """Stable hash of a skill's content (body + files) — the cache key. Changes iff
    the SOP text changes, so a cached surface is reused until the skill is edited."""
    h = hashlib.sha256()
    h.update((getattr(pack, "instructions", "") or "").encode("utf-8"))
    files = getattr(pack, "files", {}) or {}
    for k in sorted(files):
        h.update(b"\x00")
        h.update(k.encode("utf-8"))
        h.update((files[k] or "").encode("utf-8"))
    return h.hexdigest()[:16]


def chunk_skill(pack: Any) -> list[SkillChunk]:
    """Split the whole bundle (SKILL.md body + every file) into addressable chunks."""
    out: list[SkillChunk] = []
    sources = {
        "SKILL.md": getattr(pack, "instructions", "") or "",
        **(getattr(pack, "files", {}) or {}),
    }
    for fname, content in sources.items():
        for sec in split_sections(content or ""):
            body = sec.content.strip()
            gist = body.splitlines()[0][:120] if body else sec.heading
            out.append(
                SkillChunk(
                    id=f"{fname}#{sec.id}",
                    source_file=fname,
                    heading=sec.heading,
                    tokens=est_tokens(sec.content),
                    gist=gist,
                )
            )
    return out


def _message_text(msg: Any) -> str:
    """Plain text from an LlmCall result — robust across the SDK ``Message`` (a
    ``.text`` property) and a raw provider message (text lives in content blocks,
    alongside a ``thinking`` block for reasoning models)."""
    t = getattr(msg, "text", None)
    if isinstance(t, str):
        return t
    out = [
        getattr(b, "text", "") or ""
        for b in (getattr(msg, "content", None) or [])
        if getattr(b, "type", None) == "text"
    ]
    return "\n".join(out)


def _chunk_index(chunks: list[SkillChunk]) -> str:
    return "\n".join(f"- [{c.id}] {c.heading} (~{c.tokens} tok)" for c in chunks)


def deterministic_surface(pack: Any, chunks: list[SkillChunk], budget_tokens: int) -> str:
    """The no-LLM surface (the fallback): description + the chunk index to skill.read."""
    head = (
        getattr(pack, "description", "")
        or getattr(pack, "name", "")
        or getattr(pack, "id", "skill")
    ).strip()
    idx = _chunk_index(chunks)
    return (
        f"{head}\n\nThis skill's content — read a chunk with "
        f"skill.read(chunk='<id>') when a step needs it:\n{idx}"
    )


async def compile_skill(
    pack: Any, *, llm: Any = None, budget_tokens: int = DEFAULT_BUDGET
) -> CompiledSkill:
    """Compile ``pack`` into a :class:`CompiledSkill`. A bundle within ``budget_tokens``
    is its own surface (no LLM). A larger one gets an LLM-written core that references
    chunk ids; on no ``llm`` or any failure it falls back to ``deterministic_surface``."""
    chash = content_hash(pack)
    chunks = chunk_skill(pack)
    body = getattr(pack, "instructions", "") or f"SKILL: {getattr(pack, 'name', '') or pack.id}"

    # Gate on the BODY size, not the whole bundle. A well-authored SKILL.md body is
    # already a compact map even when its reference FILES are large — compiling it
    # then only ADDS tokens (skillbench A/B: deterministic/llm surfaces were bigger
    # than the body at equal accuracy). So when the body fits the budget, the body IS
    # the surface (+ a file list); the LLM core kicks in only for a large body.
    if est_tokens(body) <= budget_tokens:
        surface = body.strip()
        files = getattr(pack, "files", {}) or {}
        if files:
            surface += (
                "\n\nReference files (skill.read a section when a step needs it): "
                + ", ".join(sorted(files))
            )
        return CompiledSkill(pack.id, chash, budget_tokens, surface, tuple(chunks), "deterministic")

    surface = ""
    if llm is not None:
        idx = _chunk_index(chunks)
        user = (
            f"SKILL: {getattr(pack, 'name', '') or pack.id}\n{getattr(pack, 'description', '')}\n\n"
            f"BODY:\n{body}\n\nCHUNKS (reference by id):\n{idx}"
        )
        try:
            # Headroom over the budget: a reasoning model spends output tokens on a
            # thinking block before the surface — cap at the budget and the surface
            # comes back empty (truncated mid-thought). The SURFACE is bounded to the
            # budget below; max_tokens just needs room for reasoning + the surface.
            msg = await llm(
                stage="skill.compile",
                system=_COMPILE_PROMPT.format(budget=budget_tokens),
                messages=[{"role": "user", "content": user}],
                max_tokens=max(2048, budget_tokens * 3),
                temperature=0.0,
            )
            surface = _message_text(msg).strip()
        except Exception:
            surface = ""

    if not surface:
        return CompiledSkill(
            pack.id,
            chash,
            budget_tokens,
            deterministic_surface(pack, chunks, budget_tokens),
            tuple(chunks),
            "deterministic",
        )

    if est_tokens(surface) > budget_tokens:  # safety net — keep the surface within budget
        surface = surface[: budget_tokens * 4].rstrip() + "\n…(truncated — skill.read for detail)"
    return CompiledSkill(pack.id, chash, budget_tokens, surface, tuple(chunks), "llm")
