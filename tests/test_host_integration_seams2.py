"""Host-integration seams, round 2 — the remaining additive surfaces.

- Gap 11: ``pre_turn_gate`` — a host short-circuit (golden-cache / refusal) before
  any reasoning; returns a terminal ``AgentResult`` or ``None`` to proceed.
- Gap 10: ``Trace.tool_selection`` / ``Trace.skill_selection`` first-class fields.
- Gap 8c: the partitioned ``SemanticCache`` is public and cohort-isolated.
- Gap 5: ``PreactSpec.flow_lobe_weights`` / ``flow_layer_budgets`` named authoring
  fields fold into weights/budgets on rebuild.
"""

from __future__ import annotations

from agent_sdk import PreactAgent, SemanticCache, Skill, flow, stage, tool
from agent_sdk.clients import FakeClient
from agent_sdk.result import AgentResult
from agent_sdk.spec import PreactSpec, agent_from_spec


def _single(client, **kw):
    """A deterministic one-LLM-call agent (answer == the script's first item)."""
    return PreactAgent(
        client=client,
        instructions="bot",
        universal_memory=False,
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        stages=[stage("work", lobes=["synthesize"], loop="single")],
        **kw,
    )


# ── Gap 11: pre_turn_gate ─────────────────────────────────────────────────────
async def test_pre_turn_gate_short_circuits_before_any_llm_call():
    client = FakeClient(["MUST NOT BE USED"])

    def gate(query, state):
        return AgentResult(text="golden answer", status="answered")

    agent = _single(client, pre_turn_gate=gate)
    res = await agent.query("anything")
    assert res.text == "golden answer"
    assert client.calls == []  # the turn never reached the model


async def test_pre_turn_gate_none_proceeds():
    agent = _single(FakeClient(["real answer"]), pre_turn_gate=lambda q, s: None)
    res = await agent.query("hi")
    assert res.text == "real answer"


async def test_pre_turn_gate_async():
    async def gate(query, state):
        return AgentResult(text="async gold") if "cache" in query else None

    hit = _single(FakeClient(["miss"]), pre_turn_gate=gate)
    assert (await hit.query("cache me")).text == "async gold"
    miss = _single(FakeClient(["miss"]), pre_turn_gate=gate)
    assert (await miss.query("other")).text == "miss"


# ── Gap 10: first-class trace tool/skill selection ───────────────────────────
async def test_trace_skill_selection_first_class():
    sk = Skill("kbk", when="look things up", disclosure="on_demand", stages=["work"])
    agent = PreactAgent(
        client=FakeClient(["done"]),
        instructions="bot",
        universal_memory=False,
        skills=[sk],
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        stages=[stage("work", lobes=["synthesize"], loop="single")],
    )
    res = await agent.query("go")
    assert res.trace.skill_selection
    rec = res.trace.skill_selection[0]
    assert rec["stage"] == "work"
    assert any(r.get("label") == "kbk" for r in rec["ranking"])
    # serializes
    assert "skill_selection" in res.trace.to_json()


async def test_trace_tool_selection_first_class():
    @tool
    async def alpha(x: str) -> str:
        return "a"

    @tool
    async def beta(x: str) -> str:
        return "b"

    agent = PreactAgent(
        client=FakeClient(["done"]),
        instructions="bot",
        universal_memory=False,
        tools=[alpha, beta],
        budgets={"tool_strategy": "adaptive", "tool_budget_tokens": 50},
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        stages=[
            stage("work", lobes=["synthesize"], loop="agentic", tools=["alpha", "beta"], hops=2)
        ],
    )
    res = await agent.query("go")
    assert res.trace.tool_selection
    rec = res.trace.tool_selection[0]
    assert rec["stage"] == "work"
    assert {"kept", "hinted", "dropped"} <= set(rec)


async def test_static_default_leaves_selection_empty():
    """No adaptive routing + no on_demand skills ⇒ both lists stay empty."""
    agent = _single(FakeClient(["ok"]))
    res = await agent.query("hi")
    assert res.trace.tool_selection == []
    assert res.trace.skill_selection == []


# ── Gap 8c: partitioned SemanticCache ─────────────────────────────────────────
def test_semantic_cache_is_acl_cohort_isolated():
    c = SemanticCache()
    emb = b"\x00\x01\x02"
    c.set("ws1", "cohortA", "bge", emb, "answerA")
    assert c.get("ws1", "cohortA", "bge", emb) == "answerA"
    # SAME workspace/embedding/model but a DIFFERENT acl cohort must never hit.
    assert c.get("ws1", "cohortB", "bge", emb) is None
    # different workspace + different model also miss.
    assert c.get("ws2", "cohortA", "bge", emb) is None
    assert c.get("ws1", "cohortA", "other-model", emb) is None


# ── Gap 5: named spec flow-weight fields ──────────────────────────────────────
def test_spec_named_flow_weight_fields_roundtrip_and_fold():
    base = PreactAgent(client=FakeClient(["ok"]), instructions="bot", universal_memory=False)
    spec = base.spec()
    spec.flow_lobe_weights = {"prior_classify_simple": 0.7}
    spec.flow_layer_budgets = {"context_tokens": 1234}
    # JSON round-trip preserves the named fields.
    spec2 = PreactSpec.from_json(spec.to_json_str())
    assert spec2.flow_lobe_weights == {"prior_classify_simple": 0.7}
    assert spec2.flow_layer_budgets == {"context_tokens": 1234}
    # On rebuild they fold into the canonical weights/budgets the engine consumes.
    rebuilt = agent_from_spec(spec2, client=FakeClient(["ok"]))
    assert rebuilt.engine.weights.get("prior_classify_simple") == 0.7
    assert rebuilt.engine.budgets.get("context_tokens") == 1234
