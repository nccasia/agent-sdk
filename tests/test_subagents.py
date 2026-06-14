"""Subagent fan-out (doc 12) — the named, reusable worker + parallel/isolated/bounded map.

Covers the three "to build" items: a first-class ``Subagent`` definition + registry (in-code
and file-based), parallel + bounded-failure for the generic map, and true per-worker context
isolation; plus named delegation through the metacognition ``meta_control`` enactor.
"""

from __future__ import annotations

import pytest

from agent_sdk import PreactAgent, Subagent, SubagentRegistry, flow, probe, stage
from agent_sdk.clients.fake import scripted
from agent_sdk.subagents import load_agents_dir, parse_agent_markdown


# ── Subagent definition + registry ────────────────────────────────────────────
def test_to_item_projection_only_sets_declared_fields():
    a = Subagent("reviewer", instructions="Review.", tools=["read", "grep"], hops=5)
    item = a.to_item(input="check x.py")
    assert item == {
        "id": "reviewer", "label": "reviewer", "input": "check x.py",
        "system_prompt": "Review.", "tools": ["read", "grep"], "hops": 5,
    }
    # unset fields (lobes/model/max_tokens) are absent ⇒ they fall through to stage defaults
    assert "lobes" not in item and "model" not in item and "max_tokens" not in item


def test_empty_name_rejected():
    with pytest.raises(ValueError):
        Subagent("  ")


def test_registry_resolve_named_merges_then_inline_overrides():
    reg = SubagentRegistry([Subagent("reviewer", instructions="Review.", model="m1")])
    # named → definition spec
    assert reg.resolve_item({"agent": "reviewer", "input": "x"})["system_prompt"] == "Review."
    # inline keys override the definition
    out = reg.resolve_item({"agent": "reviewer", "input": "x", "model": "m2", "hops": 9})
    assert out["model"] == "m2" and out["hops"] == 9


def test_registry_resolve_raw_item_passes_through():
    reg = SubagentRegistry()
    assert reg.resolve_item({"label": "a", "input": "do y"}) == {"label": "a", "input": "do y"}


def test_registry_resolve_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        SubagentRegistry().resolve_item({"agent": "nope", "input": "z"})


def test_registry_add_row_and_catalog():
    reg = SubagentRegistry.from_rows([{"name": "tester", "description": "writes tests"}])
    assert "tester" in reg and reg.get("tester").description == "writes tests"
    cat = reg.render_catalog()
    assert "tester" in cat and "writes tests" in cat
    assert SubagentRegistry().render_catalog() == ""  # empty registry ⇒ no block


# ── file-based loader (.claude/agents/*.md) ───────────────────────────────────
def test_parse_agent_markdown_frontmatter_and_body():
    text = (
        "---\n"
        "name: reviewer\n"
        "description: reviews pull requests\n"
        "tools: read, grep\n"
        "model: m1\n"
        "hops: 6\n"
        "---\n"
        "You are a meticulous code reviewer.\n"
    )
    a = parse_agent_markdown(text, default_name="fallback")
    assert a.name == "reviewer" and a.description == "reviews pull requests"
    assert a.tools == ("read", "grep") and a.model == "m1" and a.hops == 6
    assert a.instructions == "You are a meticulous code reviewer."


def test_parse_agent_markdown_name_defaults_to_stem():
    a = parse_agent_markdown("no frontmatter, just a prompt body", default_name="planner")
    assert a.name == "planner" and a.instructions.startswith("no frontmatter")


def test_load_agents_dir(tmp_path):
    (tmp_path / "reviewer.md").write_text(
        "---\ndescription: reviews code\n---\nReview carefully.", encoding="utf-8"
    )
    (tmp_path / "tester.md").write_text("---\nname: qa\n---\nWrite tests.", encoding="utf-8")
    agents = load_agents_dir(tmp_path)
    names = sorted(a.name for a in agents)
    assert names == ["qa", "reviewer"]  # stem default + explicit name
    assert load_agents_dir(tmp_path / "missing") == []


# ── engine fan-out: a tool seeds the work-list, the map stage fans out ─────────
class _SeedRT:
    """Publishes a work-list (and optional failing/slow items) to the turn scratchpad."""

    name = "seedrt"

    def __init__(self, items: list[dict], *, key: str = "items"):
        self._items, self._key = items, key

    def get_tool_specs(self) -> list[dict]:
        return [{"name": "seed", "description": "seed the work-list",
                 "input_schema": {"type": "object", "properties": {}}}]

    async def call_tool(self, name, inp, retrieved_chunks=None, already_read=None) -> str:
        from agent_sdk.engine import current_turn

        current_turn().scratchpad.set(self._key, self._items)
        return "seeded"


class _GrabRT:
    """A retrieval-ish tool: appends a chunk to the worker's evidence pool, reports the pool."""

    name = "grabrt"
    seen: list[tuple[str, list[str]]] = []

    def get_tool_specs(self) -> list[dict]:
        return [{"name": "grab", "description": "retrieve a chunk",
                 "input_schema": {"type": "object", "properties": {"tag": {"type": "string"}}}}]

    async def call_tool(self, name, inp, retrieved_chunks=None, already_read=None) -> str:
        tag = str(inp.get("tag") or "x")
        if retrieved_chunks is not None:
            retrieved_chunks.append({"chunk_id": tag})
        pool = [c["chunk_id"] for c in (retrieved_chunks or [])]
        _GrabRT.seen.append((tag, pool))
        return f"pool={pool}"


def _grab_model():
    def model(sid, sy, m, t):
        last = str(m[-1]["content"]) if m else ""
        if sid == "seedstage":
            return {"tools": [{"name": "seed", "input": {}}]}
        if "Sub-task (A)" in last:
            return {"tools": [{"name": "grab", "input": {"tag": "A1"}}]}
        if "Sub-task (B)" in last:
            return {"tools": [{"name": "grab", "input": {"tag": "B1"}}]}
        return "done"

    return model


def _fanout_agent(items, *, parallel, isolated):
    return PreactAgent(
        client=scripted(_grab_model()),
        instructions="bot", tools=[_SeedRT(items), _GrabRT()],
        flows=[flow("f", stages=["seedstage", "fan"], signal={"const": 1.0})],
        stages=[
            stage("seedstage", lobes=["synthesize"], loop="agentic", tools=["seed"], hops=3),
            stage("fan", lobes=["synthesize"], loop="map", fanout_key="items", tools=["grab"],
                  fanout_parallel=parallel, fanout_isolated=isolated, hops=3),
        ],
    )


async def test_isolated_workers_have_no_cross_worker_leakage():
    _GrabRT.seen = []
    items = [{"label": "A", "input": "alpha", "tools": ["grab"]},
             {"label": "B", "input": "beta", "tools": ["grab"]}]
    agent = _fanout_agent(items, parallel=True, isolated=True)
    rec = await probe(agent, "go", label="t")
    assert rec.status == "answered"
    pools = dict(_GrabRT.seen)
    # Each worker sees ONLY its own chunk — zero cross-worker leakage.
    assert pools["A1"] == ["A1"] and pools["B1"] == ["B1"]


async def test_shared_pool_default_leaks_across_workers():
    # Parity: the default (non-isolated) sequential map shares the turn evidence pool.
    _GrabRT.seen = []
    items = [{"label": "A", "input": "alpha", "tools": ["grab"]},
             {"label": "B", "input": "beta", "tools": ["grab"]}]
    agent = _fanout_agent(items, parallel=False, isolated=False)
    await probe(agent, "go", label="t")
    pools = dict(_GrabRT.seen)
    assert pools["A1"] == ["A1"] and pools["B1"] == ["A1", "B1"]  # B sees A's chunk (shared)


async def test_parallel_and_sequential_produce_same_result_set():
    items = [{"label": "A", "input": "alpha"}, {"label": "B", "input": "beta"},
             {"label": "C", "input": "gamma"}]

    def model(sid, sy, m, t):
        last = str(m[-1]["content"]) if m else ""
        if sid == "seedstage":
            return {"tools": [{"name": "seed", "input": {}}]}
        if "Sub-task" in last:
            return "ans " + last.split("(", 1)[1].split(")", 1)[0]
        return "done"

    def build(parallel):
        return PreactAgent(
            client=scripted(model), instructions="bot", tools=[_SeedRT(items)],
            flows=[flow("f", stages=["seedstage", "fan"], signal={"const": 1.0})],
            stages=[
                stage("seedstage", lobes=["synthesize"], loop="agentic", tools=["seed"], hops=3),
                stage("fan", lobes=["synthesize"], loop="map", fanout_key="items",
                      fanout_parallel=parallel, fanout_isolated=parallel, hops=2),
            ],
        )

    seq = await probe(build(False), "go", label="s")
    par = await probe(build(True), "go", label="p")
    # Same labels answered, order-independent (parallel flushes in submission order).
    assert seq.status == par.status == "answered"
    assert {"A", "B", "C"} <= {ln.split(":")[0] for ln in par.answer.splitlines()}


async def test_bounded_failure_one_worker_times_out_others_survive():
    items = [{"label": "OK1", "input": "fine"},
             {"label": "BAD", "input": "boom", "timeout": 0.0001},
             {"label": "OK2", "input": "fine"}]

    def model(sid, sy, m, t):
        last = str(m[-1]["content"]) if m else ""
        if sid == "seedstage":
            return {"tools": [{"name": "seed", "input": {}}]}
        if "Sub-task" in last:
            return "ok " + last.split("(", 1)[1].split(")", 1)[0]
        return "done"

    agent = PreactAgent(
        client=scripted(model), instructions="bot", tools=[_SeedRT(items)],
        flows=[flow("f", stages=["seedstage", "fan"], signal={"const": 1.0})],
        stages=[
            stage("seedstage", lobes=["synthesize"], loop="agentic", tools=["seed"], hops=3),
            stage("fan", lobes=["synthesize"], loop="map", fanout_key="items",
                  fanout_parallel=True, fanout_isolated=True, hops=2),
        ],
    )
    rec = await probe(agent, "go", label="t")
    # The turn completes (degrade, never lose) and the good workers' answers survive.
    assert rec.status == "answered"
    assert "OK1" in rec.answer and "OK2" in rec.answer


# ── named delegation through metacognition ────────────────────────────────────
async def test_named_delegation_resolves_subagents_in_meta_fanout():
    from agent_sdk.plugins.subagents import SubagentsPlugin

    def model(sid, sy, m, t):
        last = str(m[-1]["content"]) if m else ""
        if sid == "meta_reflect":
            if "Sub-task" not in last and "fan_out" not in last:
                return {"tools": [{"name": "meta_control", "input": {"action": "fan_out", "items": [
                    {"agent": "reviewer", "input": "review X"},
                    {"agent": "tester", "input": "test X"},
                ]}}]}
            return "reflected"
        if "Sub-task" in last:
            return "worker saw: " + str(sy)[:80]
        return "FINAL"

    agent = PreactAgent(
        client=scripted(model), instructions="bot",
        plugins=[SubagentsPlugin([
            Subagent("reviewer", description="reviews code", instructions="You REVIEW."),
            Subagent("tester", description="writes tests", instructions="You TEST."),
        ])],
    )
    rec = await probe(agent, "step back and rethink your approach to this task", label="t")
    assert rec.status == "answered"
    reflect = [c for c in agent.client.calls if c["stage"] == "meta_reflect"]
    assert any("reviewer" in c["system"] and "tester" in c["system"] for c in reflect)
    workers = [c for c in agent.client.calls if c["stage"] == "meta_fanout"]
    systems = " ".join(c["system"] for c in workers)
    assert "You REVIEW." in systems and "You TEST." in systems  # named specs applied


async def test_unknown_subagent_name_returns_tool_error():
    from agent_sdk.plugins.subagents import SubagentsPlugin

    def model(sid, sy, m, t):
        last = str(m[-1]["content"]) if m else ""
        if sid == "meta_reflect":
            if "Error: unknown subagent" in last:
                return "acknowledged"
            if "Sub-task" not in last and "fan_out" not in last:
                return {"tools": [{"name": "meta_control", "input": {"action": "fan_out", "items": [
                    {"agent": "ghost", "input": "nope"},
                ]}}]}
            return "reflected"
        return "FINAL"

    agent = PreactAgent(
        client=scripted(model), instructions="bot",
        plugins=[SubagentsPlugin([Subagent("reviewer", description="reviews")])],
    )
    rec = await probe(agent, "step back and reconsider your approach", label="t")
    # The unknown name surfaced a clear tool error (never a silent pass); the turn still ends.
    assert rec.status == "answered"
    outputs = " ".join(str(tc.get("output", "")) for tc in rec.tool_calls)
    assert "unknown subagent" in outputs and "ghost" in outputs
