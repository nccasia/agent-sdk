"""Load a ``SKILL.md`` bundle from disk into a :class:`SkillPack`.

This is the **code-first** half of the skill story: a plugin (or an integrator)
owns its skills as ``SKILL.md`` files under a folder and loads them through this
loader at registration time, instead of the DB-row path
(:meth:`SkillRegistry.from_rows`). DB rows stay the override layer; the folder is
the source of truth for built-in skills.

A bundle is one directory:

    my_skill/
      SKILL.md            # YAML frontmatter + markdown body
      reference/notes.md  # sibling text files become layer-3 reference files

Frontmatter is real YAML (``yaml.safe_load``) so richer skills — nested
``checklist`` / ``context_vars`` block lists — parse the same way they would
from a DB row. The loaded pack records ``source_dir`` so the compiled-surface
cache can persist a sidecar next to it.

Pure of any host package: the only dependency beyond the stdlib is PyYAML.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from agent_sdk.skills.packs import SkillPack

__all__ = ["SkillLoadError", "parse_skill_md", "load_skill_pack", "load_skill_packs"]

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_TEXT_SUFFIXES = (".md", ".markdown", ".txt")


class SkillLoadError(ValueError):
    """Raised when a ``SKILL.md`` bundle cannot be parsed into a SkillPack."""


def parse_skill_md(text: str) -> tuple[dict, str]:
    """Split a ``SKILL.md`` into ``(frontmatter dict, body)``."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillLoadError("SKILL.md must start with YAML frontmatter (--- … ---)")
    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise SkillLoadError("SKILL.md frontmatter must be a YAML mapping")
    return frontmatter, text[match.end() :]


def load_skill_pack(skill_dir: Path) -> SkillPack:
    """Parse ``<skill_dir>/SKILL.md`` (+ sibling text reference files) into a
    :class:`SkillPack`. The pack ``id`` is the frontmatter ``slug`` (else the
    directory name); ``source_dir`` records where it was loaded from."""
    skill_dir = Path(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SkillLoadError(f"no SKILL.md in {skill_dir}")
    frontmatter, body = parse_skill_md(skill_md.read_text(encoding="utf-8"))

    name = str(frontmatter.get("name") or "").strip()
    description = str(frontmatter.get("description") or "").strip()
    if not name or not description:
        raise SkillLoadError(f"SKILL.md in {skill_dir} must declare name and description")

    files: dict[str, str] = {}
    for path in sorted(skill_dir.rglob("*")):
        if path == skill_md or not path.is_file():
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue  # binary/unknown entries are skipped, not imported
        files[str(path.relative_to(skill_dir))] = path.read_text(encoding="utf-8")

    def _as_str_tuple(value) -> tuple[str, ...]:
        if isinstance(value, list):
            return tuple(str(v) for v in value)
        return (str(value),) if value else ()

    def _as_dict_tuple(value) -> tuple[dict, ...]:
        if isinstance(value, list):
            return tuple(dict(v) for v in value if isinstance(v, dict))
        return ()

    slug = str(frontmatter.get("slug") or skill_dir.name)
    return SkillPack(
        id=slug,
        name=name,
        description=description,
        stages=_as_str_tuple(frontmatter.get("stages")) or ("simple_answer",),
        instructions=body.strip() or f"SKILL: {name}",
        required_tools=_as_str_tuple(frontmatter.get("required_tools")),
        injection=str(frontmatter.get("injection") or "on_demand"),
        files=files,
        checklist=_as_dict_tuple(frontmatter.get("checklist")),
        context_vars=_as_dict_tuple(frontmatter.get("context_vars")),
        source_dir=str(skill_dir),
    )


def load_skill_packs(skills_root: Path) -> list[SkillPack]:
    """Load every ``<skills_root>/<slug>/SKILL.md`` bundle (immediate subdirs).
    Returns ``[]`` when the root does not exist — a plugin with no skills."""
    skills_root = Path(skills_root)
    if not skills_root.is_dir():
        return []
    packs: list[SkillPack] = []
    for child in sorted(skills_root.iterdir()):
        if child.is_dir() and (child / "SKILL.md").is_file():
            packs.append(load_skill_pack(child))
    return packs
