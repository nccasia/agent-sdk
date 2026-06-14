"""On-demand skill activation — the ActivateSkill / skill.read / skill.search tools.

Locks the RFC 0013 progressive-disclosure path: an on-demand skill surfaces only
an index line until the model ACTIVATES it, at which point its body (or a ToC for a
large file), workspace state, and reference files become reachable — and the
activation is recorded on the session so a loaded SOP keeps driving.
"""

from __future__ import annotations

import agent_sdk as sdk
from agent_sdk.clients import FakeClient
from agent_sdk.session import Session
from agent_sdk.skill_runtime import ACTIVATE, READ, SEARCH, SkillToolRuntime
from agent_sdk.skills import SkillRegistry


def _registry(*skills: sdk.Skill) -> SkillRegistry:
    return SkillRegistry([s.to_pack() for s in skills])


def _rt(*skills: sdk.Skill) -> SkillToolRuntime:
    on_demand = [s.id for s in skills if s.disclosure == "on_demand"]
    return SkillToolRuntime(_registry(*skills), on_demand)


async def test_activate_returns_body_and_files():
    sk = sdk.Skill(
        "code_review",
        when="review code",
        disclosure="on_demand",
        instructions="SKILL: Code review\nQuote the bug and fix it.",
        files={"GUIDE.md": "## Deep checklist\nCheck the edges."},
        stages=["synthesize"],
    )
    rt = _rt(sk)
    out = await rt.call_tool(ACTIVATE, {"slug": "code_review"})
    assert "Quote the bug" in out
    assert "GUIDE.md" in out  # reference files are advertised
    assert rt.activated == ["code_review"]


async def test_activate_unknown_slug_errors():
    rt = _rt(sdk.Skill("a", when="x", disclosure="on_demand"))
    out = await rt.call_tool(ACTIVATE, {"slug": "nope"})
    assert out.startswith("Error")


async def test_read_section_and_toc():
    big = "## One\n" + ("alpha " * 50) + "\n## Two\n" + ("beta " * 50)
    sk = sdk.Skill(
        "doc",
        when="docs",
        disclosure="on_demand",
        files={"BIG.md": "x " * 4000},  # > FULL_FILE_TOKENS ⇒ ToC, not dump
        instructions=big,
    )
    rt = _rt(sk)
    # a bare read of a large file returns its table of contents
    toc = await rt.call_tool(READ, {"slug": "doc", "file": "BIG.md"})
    assert "section" in toc.lower()
    # a section read returns just that section
    sec = await rt.call_tool(READ, {"slug": "doc", "section": "one"})
    assert "alpha" in sec and "beta" not in sec
    # an unknown file is a clean error
    err = await rt.call_tool(READ, {"slug": "doc", "file": "missing.md"})
    assert err.startswith("Error")


async def test_search_locates_section():
    sk = sdk.Skill(
        "advisor",
        when="advise",
        disclosure="on_demand",
        instructions="SKILL: Advisor",
        files={"rules.md": "## Reservation\nReserve up to two semesters."},
    )
    rt = _rt(sk)
    out = await rt.call_tool(SEARCH, {"query": "reservation semesters"})
    assert "rules.md" in out and "Reservation" in out


def test_eager_skill_exposes_no_activation_tool():
    # an eager-only agent needs no ActivateSkill tool (the body is inlined)
    eager = sdk.Skill("rb", when="runbook", disclosure="eager", instructions="do X")
    agent = sdk.PreactAgent(client=FakeClient(["ok"]), instructions="b", skills=[eager])
    assert agent.engine._skill_runtime is None
    names = {s["name"] for s in (agent.engine.tools.get_tool_specs() if agent.engine.tools else [])}
    assert ACTIVATE not in names


async def test_activation_persists_to_session():
    sk = sdk.Skill(
        "code_review",
        when="review code",
        disclosure="on_demand",
        instructions="SKILL: Code review\nEnd with: — reviewed by FUNiX bot",
        stages=["synthesize"],
    )
    agent = sdk.PreactAgent(
        client=FakeClient(
            [
                {"tools": [{"name": ACTIVATE, "input": {"slug": "code_review"}}]},
                "ok — reviewed by FUNiX bot",
            ]
        ),
        instructions="bot",
        universal_memory=False,
        skills=[sk],
        flows=[sdk.flow("qna", stages=["synthesize"], signal={"const": 1.0})],
        stages=[sdk.stage("synthesize", lobes=["synthesize"], loop="agentic", hops=4)],
    )
    agent.session = Session(id="s1")
    res = await agent.query("review this code")
    assert any(
        b.get("type") == "tool_use" and b.get("name") == ACTIVATE
        for ll in res.trace.llm_calls
        for b in ll.get("response", [])
    )
    state = await agent.session.load()
    assert state.skills_in_use == ["code_review"]


async def test_no_select_prompt_is_state_aware():
    # default network (skill_select lobe in the act slice) → the lobe owns the
    # index, which teaches search→section, and ActivateSkill is exposed.
    sk = sdk.Skill(
        "code_review",
        when="review code",
        disclosure="on_demand",
        instructions="SKILL: review",
        files={"GUIDE.md": "## A\nbody"},
        stages=["act"],  # the canonical workhorse stage (was "synthesize")
    )
    agent = sdk.PreactAgent(
        client=FakeClient(["ok"]), instructions="b", universal_memory=False, skills=[sk]
    )
    res = await agent.query("review my code")
    sysp = res.trace.llm_calls[0].get("system") or ""
    assert "code_review" in sysp  # index present
    assert "skill.search" in sysp and "section" in sysp  # teaches the efficient path
    assert any(t.get("name") == ACTIVATE for t in (res.trace.llm_calls[0].get("tools") or []))


async def test_driving_suppresses_index_and_pins_workspace():
    sk = sdk.Skill(
        "code_review",
        when="review code",
        disclosure="on_demand",
        instructions="SKILL: review",
        stages=["synthesize"],
        context_vars=[{"key": "findings", "type": "notes", "title": "Findings"}],
    )
    agent = sdk.PreactAgent(
        client=FakeClient(["done"]), instructions="b", universal_memory=False, skills=[sk]
    )
    agent.session = Session(id="drv")
    st = await agent.session.load()
    st.skills_in_use = ["code_review"]  # in-memory store returns the live state
    res = await agent.query("continue the review")
    sysp = res.trace.llm_calls[0].get("system") or ""
    assert "Available skills" not in sysp  # select directive suppressed when driving
    assert "Findings" in sysp  # context_vars pinned by skill_active
    assert "follow its steps" in sysp or "Skill in use" in sysp  # drive-guide surfaced


async def test_activate_compiles_lazily_then_caches():
    # two big on-demand skills; only ONE is activated → only ONE compile call, and
    # re-activating it is a cache hit (no extra call).
    # large BODIES → the LLM core compiles (the body-size gate triggers)
    big_a = sdk.Skill(
        "adv_a",
        when="advise A",
        disclosure="on_demand",
        instructions="SKILL A\n" + ("step detail " * 120),
        stages=["synthesize"],
    )
    big_b = sdk.Skill(
        "adv_b",
        when="advise B",
        disclosure="on_demand",
        instructions="SKILL B\n" + ("step detail " * 120),
        stages=["synthesize"],
    )
    fake = FakeClient(default="CORE surface. read [SKILL.md#intro] for detail.")
    rt = SkillToolRuntime(
        _registry(big_a, big_b), ["adv_a", "adv_b"], llm=fake, budget_tokens=150, surface_mode="llm"
    )
    out1 = await rt.call_tool(ACTIVATE, {"slug": "adv_a"})
    assert "CORE surface" in out1
    assert len(fake.calls) == 1  # compiled adv_a only
    await rt.call_tool(ACTIVATE, {"slug": "adv_a"})
    assert len(fake.calls) == 1  # cache hit — no recompile
    # adv_b was never activated → never compiled
    assert all(c.get("stage") == "skill.compile" for c in fake.calls)
    assert len(fake.calls) == 1


async def test_read_resolves_chunk_id():
    sk = sdk.Skill(
        "adv",
        when="advise",
        disclosure="on_demand",
        instructions="SKILL",
        files={"ref.md": "## Reservation\nReserve up to two semesters."},
        stages=["synthesize"],
    )
    rt = SkillToolRuntime(_registry(sk), ["adv"])
    out = await rt.call_tool(READ, {"slug": "adv", "chunk": "ref.md#reservation"})
    assert "two semesters" in out


async def test_skill_tools_not_exposed_when_no_skill_for_stage():
    # a skill declared only for 'research' must NOT put ActivateSkill on a turn that
    # routes to qna/synthesize (no on-demand skill active there).
    sk = sdk.Skill(
        "research_helper",
        when="deep research",
        disclosure="on_demand",
        instructions="SKILL: research",
        stages=["research"],
    )
    agent = sdk.PreactAgent(
        client=FakeClient(["hi"]), instructions="b", universal_memory=False, skills=[sk]
    )
    res = await agent.query("hello there")  # → qna/synthesize, not research
    tools0 = {t.get("name") for t in (res.trace.llm_calls[0].get("tools") or [])}
    assert ACTIVATE not in tools0
