"""Verifies the coding agent on a REAL filesystem sandbox (no network).

Asserts the agent routes correctly, actually edits files on disk, runs the real
test suite, and reports honestly — i.e. that the SDK composes into a working
multi-stage agent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from coding_agent.agent import build_coding_agent
from coding_agent.fakes import (
    CALCULATOR_PY,
    TEST_CALCULATOR_PY,
    make_fake_client,
    make_understand_client,
)

UNDERSTAND_TASK = (
    "Explore this codebase and write an architecture document (ARCHITECTURE.md) "
    "introducing the system."
)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "calculator.py").write_text(CALCULATOR_PY)
    (tmp_path / "test_calculator.py").write_text(TEST_CALCULATOR_PY)
    return str(tmp_path)


def test_routing_is_deterministic(repo):
    agent = build_coding_agent(repo, client=make_fake_client())
    assert agent.inspect(UNDERSTAND_TASK).path[0] == "understand"
    assert agent.inspect("add a multiply function and a test").path[0] == "feature"
    assert agent.inspect("fix the broken subtract").path[0] == "quick_fix"
    # NOTE: lexical routing is literal — a question that happens to contain a
    # feature keyword (e.g. "how does add work") would mis-route to "feature".
    # See EVALUATION.md (lexical brittleness → semantic activation).
    assert agent.inspect("what does the subtract function return").path[0] == "question"


async def test_understand_pipeline_writes_architecture(repo):
    """The understand flow runs survey→plan→investigate→document on a real fs and
    writes ARCHITECTURE.md (was coding-agent-bench's deterministic probe tier —
    a scripted-model integration test, not a live bench)."""
    agent = build_coding_agent(repo, client=make_understand_client())
    await agent.query(UNDERSTAND_TASK)
    assert agent.last_trace.path["name"] == "understand"
    assert (Path(repo) / "ARCHITECTURE.md").exists()


async def test_glob_matches_root_level_files(repo):
    """Regression: `**/<file>` must match root-level files. fnmatch has no true
    `**`, so the old glob returned '(no files match)' for `**/calculator.py` when
    calculator.py sits at the repo root — the live bench wasted calls on this."""
    from coding_agent.tools import coding_tools

    tools = {t.name: t for t in coding_tools(repo)}
    g = tools["Glob"]
    assert "calculator.py" in await g.invoke({"pattern": "**/*.py"})
    assert "calculator.py" in await g.invoke({"pattern": "**/calculator.py"})
    # a sub-directory file still matches via the recursive branch
    (Path(repo) / "pkg").mkdir()
    (Path(repo) / "pkg" / "mod.py").write_text("x = 1\n")
    nested = await g.invoke({"pattern": "**/mod.py"})
    assert "pkg/mod.py" in nested
    # a non-matching pattern still returns nothing (no over-matching)
    assert await g.invoke({"pattern": "**/*.rs"}) == "(no files match)"


async def test_self_correcting_path_errors(repo):
    """A `not a file` / `not a directory` error names the closest real siblings so
    the model recovers in one hop instead of guessing again (the 17%-error tax)."""
    from coding_agent.tools import coding_tools

    tools = {t.name: t for t in coding_tools(repo)}
    out = await tools["Read"].invoke({"file_path": "calculater.py"})  # typo
    assert "Error: not a file" in out and "did you mean: calculator.py" in out
    out2 = await tools["LS"].invoke({"path": "src"})  # nonexistent dir
    assert "Error: not a directory" in out2 and ("did you mean" in out2 or "contains:" in out2)


def test_repo_map_is_deterministic_and_grounded(repo):
    from coding_agent.repomap import build_repo_map

    m1 = build_repo_map(repo)
    m2 = build_repo_map(repo)
    assert m1 == m2  # deterministic
    assert "calculator.py" in m1 and "test_calculator.py" in m1
    assert "add" in m1  # a top-level def surfaced from calculator.py


def test_feature_flow_resolves_full_pipeline(repo):
    agent = build_coding_agent(repo, client=make_fake_client())
    snap = agent.inspect("add a multiply function to calculator.py and a test")
    assert snap.flow == ["explore", "plan", "implement", "verify", "summarize"]
    activated = {lb["id"] for lb in snap.lobes if lb["activated"]}
    assert {"triage", "explore", "plan", "implement", "verify", "summarize"} <= activated


async def test_agent_edits_real_files_and_tests_pass(repo):
    agent = build_coding_agent(repo, client=make_fake_client())
    result = await agent.query("add a multiply function to calculator.py and a test for it")

    # 1. the source file was actually edited on disk
    src = (Path(repo) / "calculator.py").read_text()
    assert "def multiply(a, b):" in src
    assert "def add(a, b):" in src  # existing code preserved

    # 2. a real test file was created
    assert os.path.exists(os.path.join(repo, "test_multiply.py"))

    # 3. the agent ran the real suite and it passes
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=repo, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": repo},
    )
    assert proc.returncode == 0, proc.stdout

    # 4. the agent reports honestly
    assert "multiply" in result.text.lower()
    assert result.status == "answered"


async def test_trace_records_every_stage(repo):
    agent = build_coding_agent(repo, client=make_fake_client())
    await agent.query("add a multiply function and a test")
    stages = [s["stage"] for s in agent.last_trace.flow_stages]
    assert stages == ["explore", "plan", "implement", "verify", "summarize"]
    # explore ran tool calls (real reads)
    explore = next(s for s in agent.last_trace.flow_stages if s["stage"] == "explore")
    assert any(step["kind"] == "tool_use" for step in explore["steps"])


async def test_notes_carry_forward_across_stages(repo):
    """The implement stage should see the plan stage's output (engine carry-forward)."""
    agent = build_coding_agent(repo, client=make_fake_client())
    await agent.query("add a multiply function and a test")
    # the verify stage's system prompt should include earlier stages' notes
    verify_calls = [c for c in agent.client.calls if c["stage"] == "verify"]
    assert verify_calls
    assert "[plan]" in verify_calls[0]["system"]
    assert "[explore]" in verify_calls[0]["system"]
