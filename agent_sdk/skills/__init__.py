"""Skills — everything the agent needs to turn a Standard Operating Procedure
(a folder indexed by ``SKILL.md``) into behavior, in one module.

The pieces, by concern:

- **logic** (`packs`)      — ``SkillPack``, ``SkillRegistry``, slug/stage resolution.
- **definition** (`definition`) — the authoring façade ``Skill`` (compiles to a pack).
- **loader** (`loader`)    — ``load_skill_pack`` / ``load_skill_packs``: read a ``SKILL.md`` folder.
- **parser** (`parser`)    — sections / ToC / token estimate / ``search_bundle`` (layered reading).
- **context** (`context`)  — render a skill's live workspace state (context_vars).
- **prompt** (`prompt`)    — ``build_skill_prompt_block``: how skills enter the reasoning context.
- **tools/runtime** (`runtime`) — ``SkillToolRuntime`` (ActivateSkill / skill.read / skill.search).
- **lobes** (`lobes`)      — the OY lobes that surface (``SkillSelectLobe``) and drive
  (``SkillActiveLobe``) a skill; in ``agent_sdk.skills.lobes`` (re-exported lazily here).

The full public surface is re-exported here, so ``from agent_sdk.skills import X``
keeps working for every X it exposed as a flat module.
"""

from __future__ import annotations

from typing import Any

from agent_sdk.skills.compiler import (
    CompiledSkill,
    SkillChunk,
    chunk_skill,
    compile_skill,
    content_hash,
)
from agent_sdk.skills.context import render_context_var, render_context_vars_block
from agent_sdk.skills.definition import Skill
from agent_sdk.skills.loader import (
    SkillLoadError,
    load_skill_pack,
    load_skill_packs,
    parse_skill_md,
)
from agent_sdk.skills.packs import (
    SkillPack,
    SkillRegistry,
    merge_extra_skill_slugs,
    policy_skill_slugs,
    stage_matches,
)
from agent_sdk.skills.parser import (
    FULL_FILE_TOKENS,
    Section,
    est_tokens,
    file_purpose,
    file_toc,
    search_bundle,
    split_frontmatter,
    split_sections,
)
from agent_sdk.skills.prompt import (
    build_skill_prompt_block,
    resolve_skill_instructions,
)
from agent_sdk.skills.runtime import ACTIVATE, READ, SEARCH, SkillToolRuntime

__all__ = [
    # logic
    "SkillPack",
    "SkillRegistry",
    "policy_skill_slugs",
    "merge_extra_skill_slugs",
    "stage_matches",
    # definition
    "Skill",
    # loader (SKILL.md folder → SkillPack)
    "load_skill_pack",
    "load_skill_packs",
    "parse_skill_md",
    "SkillLoadError",
    # parser
    "Section",
    "split_sections",
    "file_toc",
    "file_purpose",
    "search_bundle",
    "split_frontmatter",
    "est_tokens",
    "FULL_FILE_TOKENS",
    # context
    "render_context_var",
    "render_context_vars_block",
    # prompt
    "build_skill_prompt_block",
    "resolve_skill_instructions",
    # runtime / tools
    "SkillToolRuntime",
    "ACTIVATE",
    "READ",
    "SEARCH",
    # compiler (LLM-built budget surface + chunk refs)
    "CompiledSkill",
    "SkillChunk",
    "compile_skill",
    "chunk_skill",
    "content_hash",
    # lobes (lazy — see __getattr__)
    "SkillSelectLobe",
    "SkillActiveLobe",
]


def __getattr__(name: str) -> Any:
    # Lazy lobe re-export: importing the lobes eagerly would create an import
    # cycle (the lobes import ``build_skill_prompt_block`` from this package), so
    # resolve them on first access instead.
    if name in ("SkillSelectLobe", "SkillActiveLobe"):
        from agent_sdk.skills import lobes

        return getattr(lobes, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
