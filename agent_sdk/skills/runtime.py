"""The skill-activation tool runtime — the model's hands on an on-demand skill.

On-demand skills (RFC 0013 progressive disclosure) surface only a one-line index
in the prompt; the model must *activate* one to load its body. This runtime
exposes the three tools that make that real:

- ``ActivateSkill(slug)`` — load the skill's instructions (or a table of contents
  when the body is large), pin its workspace state, and mark it **in use** for the
  turn (so the ``skill_active`` lobe drives it).
- ``skill.read(slug, file=…, section=…)`` — read one reference file, its ToC when
  large, or a single section — layered reading, never a whole-bundle dump.
- ``skill.search(query, slug=…)`` — keyword-search every section of the active
  skills' bundles and get back where the answer lives.

It is a plain :class:`agent_sdk.contracts.tools.ToolRuntime`; the engine composes
it in only when an on-demand skill is declared. Activation is recorded on the live
turn (``current_turn().lobe_outputs['skills_in_use']``) so the rest of the turn —
and, once the engine persists it, the next turn — reasons through the loaded SOP.
"""

from __future__ import annotations

from typing import Any

from agent_sdk.skills.context import render_context_vars_block
from agent_sdk.skills.packs import SkillRegistry
from agent_sdk.skills.parser import (
    FULL_FILE_TOKENS,
    est_tokens,
    file_toc,
    search_bundle,
    split_sections,
)

ACTIVATE = "ActivateSkill"
READ = "skill.read"
SEARCH = "skill.search"


class SkillToolRuntime:
    """A ``ToolRuntime`` exposing ActivateSkill / skill.read / skill.search over
    the on-demand skills in a registry."""

    def __init__(self, registry: SkillRegistry, slugs: list[str], *,
                 llm: Any = None, cache: Any = None, budget_tokens: int = 600,
                 surface_mode: str = "deterministic"):
        self.registry = registry
        self.slugs = list(slugs)  # the on-demand skills this runtime serves
        self.activated: list[str] = []  # slugs activated this turn (for inspection)
        # Lazy compile-on-activate seam: the first ActivateSkill builds the budget
        # surface with ``llm`` and caches it; later activations are cache hits.
        # surface_mode: "llm" (compiled core + chunk refs) | "deterministic" (no-LLM
        # chunk index) | "off" (the raw body / file_toc — the pre-surface baseline,
        # for A/B evaluation).
        self.llm = llm
        self.budget_tokens = int(budget_tokens)
        self.surface_mode = surface_mode
        if cache is None:
            from agent_sdk.skills.cache import SurfaceCache
            cache = SurfaceCache()
        self.cache = cache

    # ── specs ────────────────────────────────────────────────────────────────
    def get_tool_specs(self) -> list[dict]:
        if not self.slugs:
            return []
        return [
            {
                "name": ACTIVATE,
                "description": (
                    "Load a skill's instructions before doing its task. Activate once, "
                    "then follow the steps; don't work from the summary."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"slug": {"type": "string", "enum": list(self.slugs)}},
                    "required": ["slug"],
                },
            },
            {
                "name": READ,
                "description": (
                    "Read one referenced chunk of an activated skill: pass chunk='<id>' "
                    "(the [file#section] ids the surface points to), or file=…/section=…. "
                    "Read only the chunk a step needs — not whole files."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string", "enum": list(self.slugs)},
                        "chunk": {"type": "string"},
                        "file": {"type": "string"},
                        "section": {"type": "string"},
                    },
                    "required": ["slug"],
                },
            },
            {
                "name": SEARCH,
                "description": (
                    "Find where an answer lives in a skill's files (returns file + section "
                    "+ snippet). Use this first on a bundle, then skill.read that section."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "slug": {"type": "string", "enum": list(self.slugs)},
                    },
                    "required": ["query"],
                },
            },
        ]

    # ── activation bookkeeping ────────────────────────────────────────────────
    def _mark_in_use(self, slug: str) -> None:
        """Record ``slug`` as in use on the live turn so the ``skill_active`` lobe
        drives it (and the engine can persist it to the session)."""
        self.activated.append(slug)
        from agent_sdk.engine import current_turn

        turn = current_turn()
        outputs = getattr(turn, "lobe_outputs", None)
        if isinstance(outputs, dict):
            in_use = outputs.setdefault("skills_in_use", [])
            if isinstance(in_use, list) and slug not in in_use:
                in_use.append(slug)

    def _packs(self) -> list[Any]:
        return [p for s in self.slugs if (p := self.registry.get(s)) is not None]

    # ── dispatch ──────────────────────────────────────────────────────────────
    async def call_tool(
        self,
        name: str,
        inp: dict,
        retrieved_chunks: list[dict] | None = None,
        already_read: set[str] | None = None,
    ) -> str:
        if name == ACTIVATE:
            return await self._activate(str(inp.get("slug") or ""))
        if name == READ:
            return self._read(
                str(inp.get("slug") or ""), inp.get("file"), inp.get("section"),
                inp.get("chunk"),
            )
        if name == SEARCH:
            return self._search(str(inp.get("query") or ""), inp.get("slug"))
        return f"Error: unknown tool '{name}'."

    async def _activate(self, slug: str) -> str:
        pack = self.registry.get(slug)
        if pack is None or slug not in self.slugs:
            return f"Error: unknown skill {slug!r}. Available: {', '.join(self.slugs)}."
        self._mark_in_use(slug)
        if self.surface_mode == "off":
            surface = self._raw_surface(pack)  # pre-surface baseline (A/B)
        else:
            # Lazy compile-on-activate: reuse the cached surface, else build it now and
            # cache. "llm" ⇒ compiled core + chunk refs; "deterministic" ⇒ no-LLM index;
            # a small skill (≤ budget) is its own surface either way.
            compiled = self.cache.get(pack)
            if compiled is None:
                from agent_sdk.skills.compiler import compile_skill
                llm = self.llm if self.surface_mode == "llm" else None
                compiled = await compile_skill(pack, llm=llm, budget_tokens=self.budget_tokens)
                self.cache.put(pack, compiled)
            surface = compiled.surface
        parts = [surface]
        ctx_vars = render_context_vars_block(pack)
        if ctx_vars:
            parts.append(ctx_vars)
        return "\n\n".join(p for p in parts if p)

    def _raw_surface(self, pack: Any) -> str:
        """The pre-surface activation result: the whole body (or its ToC if large) +
        a reference-file list. The A/B baseline that the compiled surface improves on."""
        body = pack.instructions or f"SKILL: {pack.name or pack.id}"
        lead = (f"Skill '{pack.id}' (large — read sections as needed):\n" + file_toc(body)
                if est_tokens(body) > FULL_FILE_TOKENS else body)
        if pack.files:
            lead += "\n\nReference files: " + ", ".join(sorted(pack.files))
        return lead

    def _read(self, slug: str, file: Any, section: Any, chunk: Any = None) -> str:
        pack = self.registry.get(slug)
        if pack is None or slug not in self.slugs:
            return f"Error: unknown skill {slug!r}. Available: {', '.join(self.slugs)}."
        # A surface references chunks as "<file>#<section>"; accept that form directly.
        if chunk and not file and not section:
            cid = str(chunk)
            if "#" in cid:
                file, section = cid.split("#", 1)
            else:
                section = cid
        fname = str(file or "").strip()
        if not fname or fname == "SKILL.md":
            content = pack.instructions
            label = "SKILL.md"
        else:
            content = pack.files.get(fname)
            label = fname
            if content is None:
                avail = ", ".join(sorted(pack.files)) or "(none)"
                return f"Error: skill {slug!r} has no file {fname!r}. Files: {avail}."
        sections = split_sections(content)
        sec = str(section or "").strip()
        if sec:
            # Tolerant match: an LLM-written ref may carry accents/casing the slugified
            # chunk id dropped (e.g. "bảo-lưu" vs "bao-luu") — normalize both sides.
            from agent_sdk.skills.parser import _slugify_heading
            norm = _slugify_heading(sec)
            for s in sections:
                if (s.id == sec or s.heading.strip().lower() == sec.lower()
                        or s.id == norm or _slugify_heading(s.heading) == norm):
                    return s.content
            toc = file_toc(content)
            return f"Error: no section {sec!r} in {label}. {toc}"
        if est_tokens(content) > FULL_FILE_TOKENS:
            return (
                f"{label} is large — request one section with skill.read(section=…):\n"
                + file_toc(content)
            )
        return content

    def _search(self, query: str, slug: Any) -> str:
        if not query.strip():
            return "Error: search needs a query."
        packs = self._packs()
        if slug:
            packs = [p for p in packs if getattr(p, "id", None) == slug]
        hits = search_bundle(packs, query, top_k=5)
        if not hits:
            return f"(no matches for {query!r})"
        return "\n".join(
            f"- {h['skill']} · {h['file']} · [{h['section']}] {h['heading']}: {h['snippet']}"
            for h in hits
        )
