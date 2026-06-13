"""Engine robustness — truncation handling, structural allowlist enforcement,
and semantic (world-state) stall detection."""

from __future__ import annotations

from agent_sdk import PreactAgent, flow, probe, stage, tool
from agent_sdk.clients import FakeClient
from agent_sdk.clients.fake import scripted


def _agent(client, *, tools=None, stages=None, budgets=None):
    return PreactAgent(
        client=client,
        instructions="bot",
        tools=tools or [],
        budgets=budgets,
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        stages=stages or [stage("work", lobes=["synthesize"], loop="agentic", hops=8)],
    )


# ── A1: truncation is not a clean end_turn ────────────────────────────────────
async def test_truncation_retries_then_succeeds():
    # first call truncates, the in-hop retry (doubled budget) succeeds
    agent = _agent(FakeClient([{"text": "cut", "stop_reason": "max_tokens"}, "done"]))
    rec = await probe(agent, "go", label="t")
    assert rec.answer == "done"
    assert any(m.get("action") == "truncation_retry" for m in rec.meta_actions)
    assert not any(m.get("action") == "truncated_final" for m in rec.meta_actions)


async def test_persistent_truncation_is_flagged_not_swallowed():
    # always truncates → after the retry budget, the stage ends but is FLAGGED
    def always_trunc(stage_id, system, messages, tools):
        return {"text": "partial", "stop_reason": "max_tokens"}

    agent = _agent(scripted(always_trunc), budgets={"truncation_retries": 1})
    rec = await probe(agent, "go", label="t")
    assert any(m.get("action") == "truncated_final" for m in rec.meta_actions)


# ── A2: structural allowlist enforcement ──────────────────────────────────────
def _two_tools():
    @tool
    async def allowed(x: int) -> str:
        return f"allowed {x}"

    hits = []

    @tool
    async def forbidden(x: int) -> str:
        hits.append(x)
        return f"forbidden {x}"

    return allowed, forbidden, hits


async def test_unlisted_tool_refused_when_enforced():
    allowed, forbidden, hits = _two_tools()
    agent = _agent(
        FakeClient([{"tools": [{"name": "forbidden", "input": {"x": 1}}]}, "done"]),
        tools=[allowed, forbidden],
        stages=[stage("work", lobes=["synthesize"], loop="agentic", tools=["allowed"], hops=6)],
        budgets={"enforce_tool_allowlist": True},
    )
    rec = await probe(agent, "go", label="t")
    out = next(c["output"] for c in rec.tool_calls if c["name"] == "forbidden")
    assert "not available" in out
    assert hits == []  # the forbidden tool never actually executed


async def test_unlisted_tool_runs_when_not_enforced():
    allowed, forbidden, hits = _two_tools()
    agent = _agent(
        FakeClient([{"tools": [{"name": "forbidden", "input": {"x": 1}}]}, "done"]),
        tools=[allowed, forbidden],
        stages=[stage("work", lobes=["synthesize"], loop="agentic", tools=["allowed"], hops=6)],
        budgets=None,  # default: enforcement off → unchanged behavior
    )
    await probe(agent, "go", label="t")
    assert hits == [1]  # executes as before


# ── A3: semantic (world-state) stall ──────────────────────────────────────────
async def test_repeated_error_results_count_as_no_progress():
    # a tool that always errors makes no progress → stall-break fires
    @tool
    async def look(path: str) -> str:
        return "Error: not a file: " + path

    agent = _agent(
        scripted(lambda s, sy, m, t: ("done" if not t else {"tools": [{"name": "look", "input": {"path": "x"}}]})),
        tools=[look],
        stages=[stage("work", lobes=["synthesize"], loop="agentic", tools=["look"], hops=40)],
        budgets={"stall_patience": 2},
    )
    rec = await probe(agent, "go", label="t")
    assert any(m.get("action") == "stall_break" for m in rec.meta_actions)
    assert len(rec.llm_calls) <= 6  # broke early, didn't run to the 40-hop ceiling


async def test_novel_results_keep_progressing():
    # each hop reads a NEW file (distinct non-error output) → never stalls
    @tool
    async def look(path: str) -> str:
        return f"contents of {path}"

    script = [{"tools": [{"name": "look", "input": {"path": f"f{i}.py"}}]} for i in range(8)] + ["done"]
    agent = _agent(
        FakeClient(script),
        tools=[look],
        stages=[stage("work", lobes=["synthesize"], loop="agentic", tools=["look"], hops=40)],
        budgets={"stall_patience": 2},
    )
    rec = await probe(agent, "go", label="t")
    assert not any(m.get("action") == "stall_break" for m in rec.meta_actions)
    assert rec.answer == "done"
