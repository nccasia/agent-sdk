"""Load a ``SKILL.md`` folder into an SDK :class:`agent_sdk.Skill`.

A skill is a *folder whose index is ``SKILL.md``* (the SOP standard from
``docs/concepts/skills.md``): YAML frontmatter + a markdown body, plus sibling
``*.md`` / ``*.txt`` reference files that become the skill's ``files`` bundle.

This is the bench's own loader — it mirrors agent-core's ``load_skill_pack`` but
imports only ``agent_sdk``, keeping skillbench self-contained (the SDK-bench
convention: no project/agent-core imports).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from agent_sdk import Skill

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_TEXT_SUFFIXES = (".md", ".markdown", ".txt")


class SkillLoadError(ValueError):
    """A ``SKILL.md`` folder could not be parsed into a Skill."""


def parse_skill_md(text: str) -> tuple[dict, str]:
    """Split a SKILL.md into (frontmatter dict, body)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillLoadError("SKILL.md must start with YAML frontmatter (--- … ---)")
    front = yaml.safe_load(match.group(1)) or {}
    if not isinstance(front, dict):
        raise SkillLoadError("SKILL.md frontmatter must be a YAML mapping")
    return front, text[match.end() :]


def _str_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)] if value else []


def _dict_list(value) -> list[dict]:
    return [dict(v) for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def load_skill(skill_dir: Path) -> Skill:
    """Parse ``<skill_dir>/SKILL.md`` (+ sibling reference files) into a Skill.

    The skill ``id`` is the frontmatter ``slug`` (else the directory name); the
    ``files`` bundle is every sibling ``*.md``/``*.txt`` keyed by its path
    relative to the folder.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SkillLoadError(f"no SKILL.md in {skill_dir}")
    front, body = parse_skill_md(skill_md.read_text(encoding="utf-8"))

    name = str(front.get("name") or "").strip()
    description = str(front.get("description") or "").strip()
    if not name or not description:
        raise SkillLoadError(f"SKILL.md in {skill_dir} must declare name and description")

    files: dict[str, str] = {}
    for path in sorted(skill_dir.rglob("*")):
        if path == skill_md or not path.is_file():
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        files[str(path.relative_to(skill_dir))] = path.read_text(encoding="utf-8")

    disclosure = str(front.get("injection") or "on_demand")
    return Skill(
        str(front.get("slug") or skill_dir.name),
        name=name,
        description=description,
        when=description,
        instructions=body.strip() or f"SKILL: {name}",
        tools=_str_list(front.get("required_tools")),
        disclosure=disclosure,
        files=files,
        stages=_str_list(front.get("stages")) or ["synthesize"],
        checklist=_dict_list(front.get("checklist")),
        context_vars=_dict_list(front.get("context_vars")),
        source_dir=str(skill_dir),  # lets the compiled-surface cache persist a sidecar
    )


def load_skills(skills_root: Path) -> list[Skill]:
    """Load every ``<skills_root>/<slug>/SKILL.md`` folder (immediate subdirs).

    Folders are returned sorted by name; a folder missing a ``SKILL.md`` is
    skipped. Each is loaded independently so one bad fixture does not sink the
    rest — a load error is re-raised with the folder name for the caller to
    surface as a ``parse`` failure.
    """
    skills: list[Skill] = []
    for child in sorted(skills_root.iterdir()):
        if child.is_dir() and (child / "SKILL.md").is_file():
            skills.append(load_skill(child))
    return skills
