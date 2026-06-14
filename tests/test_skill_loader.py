"""SKILL.md folder → SkillPack loader (agent_sdk.skills.loader)."""

from __future__ import annotations

import pytest

from agent_sdk.skills import (
    SkillLoadError,
    load_skill_pack,
    load_skill_packs,
    parse_skill_md,
)

_SKILL_MD = """\
---
name: Code review
description: Review a diff for correctness and style.
slug: code_review
injection: on_demand
stages:
  - synthesize
  - research
required_tools:
  - kb.retrieve
checklist:
  - key: scope
    title: Confirm scope
context_vars:
  - key: notes
    type: notes
    title: Review notes
---
# Code review

Read the diff, then comment on correctness first.
"""


def test_parse_skill_md_splits_frontmatter_and_body():
    front, body = parse_skill_md(_SKILL_MD)
    assert front["name"] == "Code review"
    assert front["slug"] == "code_review"
    assert body.strip().startswith("# Code review")


def test_parse_skill_md_requires_frontmatter():
    with pytest.raises(SkillLoadError):
        parse_skill_md("no frontmatter here")


def test_load_skill_pack_reads_folder(tmp_path):
    d = tmp_path / "code_review"
    d.mkdir()
    (d / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    (d / "reference.md").write_text("# Reference\nDetail here.", encoding="utf-8")

    pack = load_skill_pack(d)

    assert pack.id == "code_review"
    assert pack.name == "Code review"
    assert pack.injection == "on_demand"
    assert pack.stages == ("synthesize", "research")
    assert pack.required_tools == ("kb.retrieve",)
    assert pack.checklist == ({"key": "scope", "title": "Confirm scope"},)
    assert pack.context_vars == ({"key": "notes", "type": "notes", "title": "Review notes"},)
    assert pack.files == {"reference.md": "# Reference\nDetail here."}
    assert pack.source_dir == str(d)
    # the nested checklist surfaces uniformly through the pack's own accessor
    assert any(v.get("type") == "checklist" for v in pack.all_context_vars())


def test_load_skill_pack_requires_name_and_description(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nslug: broken\n---\nbody", encoding="utf-8")
    with pytest.raises(SkillLoadError):
        load_skill_pack(d)


def test_load_skill_pack_missing_file(tmp_path):
    with pytest.raises(SkillLoadError):
        load_skill_pack(tmp_path / "nope")


def test_load_skill_packs_scans_immediate_subdirs(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    (a / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    b = tmp_path / "b"
    b.mkdir()  # no SKILL.md → skipped
    (tmp_path / "loose.txt").write_text("ignored", encoding="utf-8")

    packs = load_skill_packs(tmp_path)
    assert [p.id for p in packs] == ["code_review"]


def test_load_skill_packs_missing_root_is_empty(tmp_path):
    assert load_skill_packs(tmp_path / "nope") == []
