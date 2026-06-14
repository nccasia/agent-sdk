"""Host-integration seams — the additive surfaces agent-core builds on.

Covers four gaps that let a host (agent-core) assemble ON the SDK without forking
the engine:

- Gap 1: the turn's shared EVIDENCE CHANNEL (``retrieved_chunks`` / ``already_read``)
  is threaded into every ``call_tool`` so a KB runtime accumulates a pool across
  stages/hops (the substrate the cite/filter grounding reads).
- Gap 2: ``AgentSetup.add_tool_runtime`` + ``setup.host`` — a plugin mounts a whole,
  host-bound stateful runtime (ahead of @tool fns for namespaced first-wins).
- Gap 4: ``PreactAgent(context=…)`` — an opaque identity/channel bag landing on
  every ``TurnContext.identity`` / ``.channel`` (the ACL seam).
- Gap 7: the ``Skill`` façade + spec round-trip carry ``checklist`` / ``context_vars``.
"""

from __future__ import annotations

from agent_sdk import PreactAgent, Skill, flow, probe, stage, tool
from agent_sdk.clients import FakeClient
from agent_sdk.engine import current_turn
from agent_sdk.plugins.base import AgentSetup
from agent_sdk.spec import build_spec


def _agentic(tool_name: str, runtime, *, hops: int = 10, **kw):
    """A one-stage agentic agent exposing ``runtime`` under ``tool_name``."""
    return PreactAgent(
        instructions="bot",
        tools=[runtime],
        universal_memory=False,
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        stages=[stage("work", lobes=["synthesize"], loop="agentic", tools=[tool_name], hops=hops)],
        **kw,
    )


# ── Gap 1: evidence channel ───────────────────────────────────────────────────
class _KBRuntime:
    """A KB-style runtime that appends to the shared pool and dedupes by id."""

    def __init__(self) -> None:
        self.observed_sizes: list[int] = []
        self.observed_already: list[set] = []

    def get_tool_specs(self) -> list[dict]:
        return [
            {
                "name": "kb_search",
                "description": "search the KB",
                "input_schema": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            }
        ]

    async def call_tool(self, name, inp, retrieved_chunks, already_read):
        self.observed_sizes.append(len(retrieved_chunks))
        self.observed_already.append(set(already_read))
        cid = f"c{len(retrieved_chunks)}"
        if cid not in already_read:
            retrieved_chunks.append({"chunk_id": cid, "text": f"chunk for {inp.get('q')}"})
            already_read.add(cid)
        return f"hit {cid}"


async def test_evidence_channel_accumulates_across_hops():
    kb = _KBRuntime()
    agent = _agentic(
        "kb_search",
        kb,
        client=FakeClient(
            [
                {"tools": [{"name": "kb_search", "input": {"q": "a"}}]},
                {"tools": [{"name": "kb_search", "input": {"q": "b"}}]},
                "final answer",
            ]
        ),
    )
    rec = await probe(agent, "go", label="t")
    assert rec.status == "answered"
    # The SAME pool was threaded into both hops: it grew 0 → 1 (not reset to 0
    # each call, which is what the old hardcoded `[], set()` produced).
    assert kb.observed_sizes == [0, 1]
    assert "c0" in kb.observed_already[1]


async def test_degraded_markers_surface_on_trace():
    """A host tool appends an infra-degradation marker via current_turn().degraded."""

    class _DegradingRuntime:
        def get_tool_specs(self):
            return [
                {
                    "name": "kb_search",
                    "description": "d",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ]

        async def call_tool(self, name, inp, retrieved_chunks, already_read):
            current_turn().degraded.append("retrieval:no_readers")
            return "(no readers)"

    agent = _agentic(
        "kb_search",
        _DegradingRuntime(),
        client=FakeClient(
            [
                {"tools": [{"name": "kb_search", "input": {}}]},
                "done",
            ]
        ),
    )
    res = await agent.query("go")
    assert res.status == "answered"
    assert "retrieval:no_readers" in res.trace.degraded
    # and it round-trips in the persisted JSON
    assert res.trace.to_json()["degraded"] == ["retrieval:no_readers"]


async def test_evidence_pool_exposed_on_turn_context():
    """A grounding lobe/tool can read the accumulated pool via current_turn()."""
    seen: dict = {}

    class _Reader:
        def get_tool_specs(self):
            return [
                {
                    "name": "kb_search",
                    "description": "d",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ]

        async def call_tool(self, name, inp, retrieved_chunks, already_read):
            retrieved_chunks.append({"chunk_id": "x1", "text": "t"})
            already_read.add("x1")
            turn = current_turn()
            seen["chunks"] = list(turn.retrieved_chunks)
            seen["read"] = set(turn.already_read)
            return "ok"

    agent = _agentic(
        "kb_search",
        _Reader(),
        client=FakeClient(
            [
                {"tools": [{"name": "kb_search", "input": {}}]},
                "done",
            ]
        ),
    )
    await probe(agent, "go", label="t")
    # current_turn().retrieved_chunks IS the pool the runtime mutated.
    assert seen["chunks"] == [{"chunk_id": "x1", "text": "t"}]
    assert seen["read"] == {"x1"}


# ── Gap 2: add_tool_runtime + host ────────────────────────────────────────────
def test_agentsetup_add_tool_runtime_and_host():
    s = AgentSetup()
    assert s.host is None and s.tool_runtimes == []
    rt = object()
    s.add_tool_runtime(rt)
    assert s.tool_runtimes == [rt]


class _HostBoundRuntime:
    def __init__(self, host):
        self.host = host

    def get_tool_specs(self):
        return [
            {
                "name": "host_tool",
                "description": "d",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

    async def call_tool(self, name, inp, retrieved_chunks, already_read):
        return f"host={self.host}"


class _HostPlugin:
    name = "hostp"

    def install(self, setup):
        setup.add_tool_runtime(_HostBoundRuntime(setup.host))


async def test_plugin_mounts_host_bound_runtime():
    agent = PreactAgent(
        client=FakeClient(["ok"]),
        instructions="bot",
        plugins=[_HostPlugin()],
        host="HOST-123",
        universal_memory=False,
    )
    names = {s["name"] for s in agent.engine.tools.get_tool_specs()}
    assert "host_tool" in names
    out = await agent.engine.tools.call_tool("host_tool", {}, [], set())
    assert out == "host=HOST-123"


async def test_priority_runtime_wins_name_collision():
    """A mounted runtime wins the first-seen-name dedup over a @tool fn."""

    @tool
    async def kb_search(q: str) -> str:
        return "from fn"

    class _RT:
        def get_tool_specs(self):
            return [
                {
                    "name": "kb_search",
                    "description": "rt",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ]

        async def call_tool(self, name, inp, retrieved_chunks, already_read):
            return "from runtime"

    composed = PreactAgent._compose_tools([kb_search], None, priority_runtimes=[_RT()])
    assert await composed.call_tool("kb_search", {}, [], set()) == "from runtime"


# ── Gap 4: context bag ────────────────────────────────────────────────────────
async def test_context_bag_lands_on_turn_identity():
    seen: dict = {}

    class _IdentityProbe:
        def get_tool_specs(self):
            return [
                {
                    "name": "whoami",
                    "description": "d",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ]

        async def call_tool(self, name, inp, retrieved_chunks, already_read):
            t = current_turn()
            seen["identity"] = dict(t.identity)
            seen["channel"] = dict(t.channel)
            return "ok"

    agent = _agentic(
        "whoami",
        _IdentityProbe(),
        client=FakeClient([{"tools": [{"name": "whoami", "input": {}}]}, "done"]),
        context={"identity": {"tenant_id": "t1", "user_id": "u9"}, "channel": {"channel_id": "c5"}},
    )
    await probe(agent, "go", label="t")
    assert seen["identity"] == {"tenant_id": "t1", "user_id": "u9"}
    assert seen["channel"] == {"channel_id": "c5"}


# ── Gap 7: skill checklist / context_vars ─────────────────────────────────────
def test_skill_facade_carries_checklist_and_context_vars():
    sk = Skill(
        "wizard",
        when="onboarding",
        checklist=[{"key": "name", "ask": "Your name?"}],
        context_vars=[{"key": "notes", "type": "notes", "title": "Notes"}],
    )
    pack = sk.to_pack()
    assert pack.checklist == ({"key": "name", "ask": "Your name?"},)
    assert pack.context_vars == ({"key": "notes", "type": "notes", "title": "Notes"},)
    # all_context_vars folds the legacy checklist in as a type:checklist var.
    keys = {v["key"] for v in pack.all_context_vars()}
    assert {"checklist", "notes"} <= keys


async def test_skill_spec_roundtrip_preserves_wizard_fields():
    agent = PreactAgent(
        client=FakeClient(["ok"]),
        instructions="bot",
        universal_memory=False,
        skills=[
            Skill(
                "wizard",
                when="onboarding",
                checklist=[{"key": "name", "ask": "Your name?"}],
                context_vars=[{"key": "notes", "type": "notes"}],
            )
        ],
    )
    spec = build_spec(agent)
    row = next(r for r in spec.skills if r["id"] == "wizard")
    assert row["checklist"] == [{"key": "name", "ask": "Your name?"}]
    assert row["context_vars"] == [{"key": "notes", "type": "notes"}]
    # Rebuild from spec → the pack still carries them.
    rebuilt = PreactAgent.from_spec(spec, client=FakeClient(["ok"]))
    pack = next(p for p in rebuilt.engine.skill_packs if p.id == "wizard")
    assert pack.checklist == ({"key": "name", "ask": "Your name?"},)
    assert pack.context_vars == ({"key": "notes", "type": "notes"},)
