"""Phase 4 — adaptive tool exposure via the selection-hint API.

A stage that exposes a broad toolset routes its tools to inject/hint/drop by
relevance under a token budget, with an essentials guard (the stage allowlist +
``memory`` are never dropped). Default (``tool_strategy != "adaptive"`` / no
budget) keeps every tool — byte-identical, cache-stable.
"""

from __future__ import annotations

from agent_sdk import PreactAgent, flow, probe, stage, tool
from agent_sdk.clients import FakeClient
from agent_sdk.memory.durable import Memory


def _tools():
    @tool
    async def search_kb(q: str) -> str:
        "search the knowledge base for facts and documents"
        return "hit"

    @tool
    async def weather(city: str) -> str:
        "current weather forecast for a city"
        return "sunny"

    @tool
    async def calculator(expr: str) -> str:
        "evaluate an arithmetic expression"
        return "42"

    @tool
    async def translate(text: str) -> str:
        "translate text between languages"
        return "ok"

    @tool
    async def send_email(to: str) -> str:
        "send an email message to a recipient"
        return "sent"

    return [search_kb, weather, calculator, translate, send_email]


def _agent(*, adaptive: bool):
    budgets = {"tool_strategy": "adaptive", "tool_budget_tokens": 4000} if adaptive else {}
    return PreactAgent(
        client=FakeClient(["done"]),
        instructions="bot",
        tools=_tools(),
        memory=Memory(),  # contributes the always-kept `memory` tool
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        # no `tools=` allowlist → the stage exposes the full toolset to selection
        stages=[stage("work", lobes=["synthesize"], loop="agentic")],
        budgets=budgets,
    )


def test_default_keeps_all_tools_byte_identical():
    agent = _agent(adaptive=False)
    st = agent.engine.stage_registry.resolve(["work"])[0]
    specs, record = agent.engine._select_tools(st, "search the knowledge base")
    assert record == {}  # selection inert
    assert specs == agent.engine._tool_specs(st)  # payload unchanged


def test_adaptive_drops_irrelevant_keeps_essentials_and_relevant():
    agent = _agent(adaptive=True)
    st = agent.engine.stage_registry.resolve(["work"])[0]
    specs, record = agent.engine._select_tools(st, "search the knowledge base for facts")
    kept = {s["name"] for s in specs}
    assert "memory" in kept  # essentials guard — never dropped
    assert "search_kb" in kept  # the relevant tool stays
    assert record["dropped"], "an irrelevant tool should drop under the budget"
    # nothing dropped is still in the payload
    assert not (set(record["dropped"]) & kept)


async def test_tool_selection_recorded_in_trace():
    rec = await probe(_agent(adaptive=True), "search the knowledge base for facts", label="t")
    work = next(s for s in rec.stages if s["stage"] == "work")
    sel = work["metadata"]["tool_selection"]
    assert set(sel) == {"kept", "hinted", "dropped"}
    assert "search_kb" in (sel["kept"] + sel["hinted"])


async def test_payload_stable_across_hops():
    """Selection is computed once per stage → the tool payload (and thus the
    cached prefix) is identical on every hop of the stage."""
    @tool
    async def search_kb(q: str) -> str:
        "search the knowledge base"
        return "more"

    agent = PreactAgent(
        client=FakeClient(
            [{"tools": [{"name": "search_kb", "input": {"q": "x"}}]} for _ in range(3)] + ["done"]
        ),
        instructions="bot",
        tools=[search_kb, *_tools()],
        memory=Memory(),
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        stages=[stage("work", lobes=["synthesize"], loop="agentic", hops=6)],
        budgets={"tool_strategy": "adaptive", "tool_budget_tokens": 4000},
    )
    rec = await probe(agent, "search the knowledge base repeatedly", label="t")
    # the per-hop tool payloads (recorded on each llm_call) are identical
    payloads = [tuple(sorted(t["name"] for t in c.get("tools", [])))
                for c in rec.llm_calls if c["stage"] == "work" and c.get("tools")]
    assert payloads and len(set(payloads)) == 1  # stable across hops
