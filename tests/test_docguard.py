"""Phase 5 — heavy-output write discipline (the DocWriteGuard tool filter)."""

from __future__ import annotations

from agent_sdk import DocWriteGuard, PreactAgent, flow, probe, stage, tool
from agent_sdk.clients import FakeClient


# ── the guard in isolation ────────────────────────────────────────────────────
def test_repeated_full_write_is_steered():
    g = DocWriteGuard(write_tools=("write_file",))
    assert g("document", "write_file", {"path": "ARCHITECTURE.md", "content": "v1"}) is None
    out = g("document", "write_file", {"path": "ARCHITECTURE.md", "content": "v2"})
    assert out is not None and "already written" in out
    assert g.events == [{"stage": "document", "path": "ARCHITECTURE.md", "action": "redundant_rewrite"}]


def test_different_paths_not_flagged():
    g = DocWriteGuard(write_tools=("write_file",))
    assert g("document", "write_file", {"path": "A.md", "content": "x"}) is None
    assert g("document", "write_file", {"path": "B.md", "content": "y"}) is None
    assert g.events == []


def test_write_tool_refused_in_readonly_stage():
    # the per-stage allowlist only hides the spec — the guard must refuse a write
    # tool the model calls anyway in a read-only stage (the live survey-writes bug)
    g = DocWriteGuard(write_tools=("Write",), readonly_stages=("survey",))
    out = g("survey", "Write", {"file_path": "ARCHITECTURE.md", "content": "x"})
    assert out is not None and "read-only" in out
    assert g.events[-1]["action"] == "blocked_readonly_write"
    # a writable stage is unaffected — the first write goes through
    assert g("document", "Write", {"file_path": "ARCHITECTURE.md", "content": "x"}) is None


def test_file_path_key_is_recognized():
    # the coding agent's Write uses `file_path`; default path_keys must catch it
    g = DocWriteGuard(write_tools=("Write",))
    assert g("document", "Write", {"file_path": "A.md", "content": "v1"}) is None
    out = g("document", "Write", {"file_path": "A.md", "content": "v2"})
    assert out is not None and "already written" in out


def test_cross_stage_rewrite_is_steered():
    g = DocWriteGuard(write_tools=("write_file",))
    # produced once in an earlier stage…
    assert g("investigate", "write_file", {"path": "ARCHITECTURE.md", "content": "v1"}) is None
    # …then re-derived from scratch in a later stage → steered (not a within-stage repeat)
    out = g("document", "write_file", {"path": "ARCHITECTURE.md", "content": "v2"})
    assert out is not None and "earlier step" in out
    assert g.events[-1]["action"] == "redundant_rewrite_cross_stage"


def test_bash_write_blocked_in_readonly_stage():
    g = DocWriteGuard(bash_tool="bash", readonly_stages=("survey",))
    out = g("survey", "bash", {"command": "cat > ARCHITECTURE.md << 'EOF'\n# Doc\nEOF"})
    assert out is not None and "read-only" in out
    assert g.events[0]["action"] == "blocked_readonly_write"
    # a non-write bash command in the same stage is allowed
    assert g("survey", "bash", {"command": "ls -la && grep foo *.py"}) is None


def test_record_only_measures_without_intercepting():
    g = DocWriteGuard(write_tools=("write_file",), record_only=True)
    g("document", "write_file", {"path": "A.md", "content": "v1"})
    out = g("document", "write_file", {"path": "A.md", "content": "v2"})
    assert out is None  # not intercepted (measure-only)
    assert g.events and g.events[0]["action"] == "redundant_rewrite"


# ── wired into the engine via the tool-filter seam ────────────────────────────
async def test_guard_intercepts_in_engine():
    @tool
    async def write_file(path: str, content: str) -> str:
        return f"wrote {len(content)} bytes to {path}"

    guard = DocWriteGuard(write_tools=("write_file",))
    agent = PreactAgent(
        client=FakeClient([
            {"tools": [{"name": "write_file", "input": {"path": "DOC.md", "content": "first"}}]},
            {"tools": [{"name": "write_file", "input": {"path": "DOC.md", "content": "second"}}]},
            "done",
        ]),
        instructions="bot",
        tools=[write_file],
        tool_filters=[guard],
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        stages=[stage("work", lobes=["synthesize"], loop="agentic", tools=["write_file"], hops=6)],
    )
    rec = await probe(agent, "write the doc", label="t")
    outputs = [c["output"] for c in rec.tool_calls if c["name"] == "write_file"]
    assert any("wrote" in o for o in outputs)  # the first write executed
    assert any("already written" in o for o in outputs)  # the second was steered
    assert guard.events and guard.events[0]["action"] == "redundant_rewrite"


async def test_no_filters_default_unchanged():
    @tool
    async def write_file(path: str, content: str) -> str:
        return "ok"

    agent = PreactAgent(
        client=FakeClient([
            {"tools": [{"name": "write_file", "input": {"path": "D.md", "content": "a"}}]},
            {"tools": [{"name": "write_file", "input": {"path": "D.md", "content": "b"}}]},
            "done",
        ]),
        instructions="bot", tools=[write_file],
        flows=[flow("qna", stages=["work"], signal={"const": 1.0})],
        stages=[stage("work", lobes=["synthesize"], loop="agentic", tools=["write_file"], hops=6)],
    )
    rec = await probe(agent, "write", label="t")
    # no guard → both writes execute unchanged
    assert [c["output"] for c in rec.tool_calls if c["name"] == "write_file"] == ["ok", "ok"]
