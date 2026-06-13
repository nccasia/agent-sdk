"""Skill compiler — budget-bounded surface + chunk references, lazily built."""

from __future__ import annotations

import json

import agent_sdk as sdk
from agent_sdk.clients import FakeClient
from agent_sdk.skills.cache import SurfaceCache
from agent_sdk.skills.compiler import (
    CompiledSkill,
    chunk_skill,
    compile_skill,
    content_hash,
)
from agent_sdk.skills.parser import est_tokens


def _big_skill(slug="advisor"):
    # LARGE BODY (> budget) → the LLM core kicks in; the big file gives chunk refs.
    return sdk.Skill(
        slug, when="advise", disclosure="on_demand",
        instructions="SKILL: Advisor\n" + ("Follow the detailed procedure carefully. " * 120),
        files={"reference/catalog.md": "## ML\n" + ("course prerequisites " * 400)},
        stages=["synthesize"],
    ).to_pack()


def _small_skill(slug="cr"):
    return sdk.Skill(slug, when="review", disclosure="on_demand",
                     instructions="SKILL: review\nquote the bug", stages=["synthesize"]).to_pack()


async def test_small_skill_is_deterministic_no_llm():
    pack = _small_skill()
    seen = []
    c = await compile_skill(pack, llm=None, budget_tokens=600)
    assert c.built_by == "deterministic"
    assert "quote the bug" in c.surface
    assert not seen  # no llm involved


async def test_large_skill_llm_surface_within_budget():
    pack = _big_skill()
    fake = FakeClient(["CORE: advise on courses. For ML prereqs read [reference/catalog.md#ml]."])
    c = await compile_skill(pack, llm=fake, budget_tokens=200)
    assert c.built_by == "llm"
    assert "[reference/catalog.md#ml]" in c.surface
    assert est_tokens(c.surface) <= 200
    assert any(ch.id == "reference/catalog.md#ml" for ch in c.chunks)
    assert c.content_hash and len(c.content_hash) == 16


async def test_large_skill_falls_back_on_llm_error():
    pack = _big_skill()

    async def boom(**kw):
        raise RuntimeError("provider down")

    c = await compile_skill(pack, llm=boom, budget_tokens=200)
    assert c.built_by == "deterministic"
    assert "reference/catalog.md#ml" in c.surface  # chunk index still points the model


def test_chunk_ids_match_sections_and_are_readable():
    pack = _big_skill()
    chunks = chunk_skill(pack)
    ids = {c.id for c in chunks}
    assert "SKILL.md#intro" in ids or any(c.source_file == "SKILL.md" for c in chunks)
    assert "reference/catalog.md#ml" in ids


def test_content_hash_changes_on_edit():
    a = _small_skill()
    b = sdk.Skill("cr", when="review", disclosure="on_demand",
                  instructions="SKILL: review\nDIFFERENT", stages=["synthesize"]).to_pack()
    assert content_hash(a) != content_hash(b)


def test_sidecar_roundtrip_and_stale(tmp_path):
    pack = sdk.Skill("cr", when="review", disclosure="on_demand",
                     instructions="SKILL: review", stages=["synthesize"],
                     source_dir=str(tmp_path)).to_pack()
    cache = SurfaceCache()
    compiled = CompiledSkill(pack.id, content_hash(pack), 600, "the surface", (), "deterministic")
    cache.put(pack, compiled)
    assert (tmp_path / "SKILL.compiled.json").is_file()
    # fresh cache loads it from the sidecar (hash matches)
    assert SurfaceCache().get(pack).surface == "the surface"
    # corrupt the sidecar's hash → stale → ignored
    p = tmp_path / "SKILL.compiled.json"
    d = json.loads(p.read_text())
    d["content_hash"] = "deadbeef"
    p.write_text(json.dumps(d))
    assert SurfaceCache().get(pack) is None
