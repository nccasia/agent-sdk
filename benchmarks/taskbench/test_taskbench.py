"""Deterministic offline checks (no LLM): the seed, every task's reference SQL,
the real tools, and the grader. Validates the harness + ground truth before any
live run — a broken answer_sql would make the live grade meaningless."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).parent


def _mod(name):
    spec = importlib.util.spec_from_file_location("tb_" + name, HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


seed = _mod("seed")
grade = _mod("grade")
sqlite_plugin = _mod("sqlite_plugin")


def _tasks():
    return [json.loads(x) for x in (HERE / "dataset" / "tasks.jsonl").read_text().splitlines() if x.strip()]


def _run(coro):
    return asyncio.run(coro)


def test_seed_is_deterministic():
    a = seed.build_db().execute("SELECT COUNT(*), SUM(quantity*unit_price) FROM order_items").fetchone()
    b = seed.build_db().execute("SELECT COUNT(*), SUM(quantity*unit_price) FROM order_items").fetchone()
    assert a == b and a[0] > 0


def test_every_task_reference_sql_yields_ground_truth():
    conn = seed.build_db()
    for t in _tasks():
        facts = grade.ground_truth(conn, t["answer_sql"])
        assert facts, f"{t['id']}: answer_sql returned no rows"
        assert any(f is not None for f in facts), f"{t['id']}: all ground-truth facts are NULL"


def _sqlite_tools(store):
    from agent_sdk.plugins.base import AgentSetup
    setup = AgentSetup()
    sqlite_plugin.SqlitePlugin(store).install(setup)
    return {t.name: t for t in setup.tools if hasattr(t, "name")}


def test_db_query_tool_runs_and_errors_cleanly():
    store = sqlite_plugin.SqliteStore(seed.build_db())
    tools = _sqlite_tools(store)
    assert "60" in _run(tools["db.query"].invoke({"sql": "SELECT COUNT(*) FROM customers"}))
    bad = _run(tools["db.query"].invoke({"sql": "SELECT nope FROM customers"}))
    assert bad.startswith("Error:") and len(store.queries) == 2 and "error" in store.queries[1]
    assert "read-only" in _run(tools["db.query"].invoke({"sql": "DELETE FROM customers"}))


def test_grader_matches_facts_with_tolerance():
    ok, _ = grade.grade_answer("The top products are Laptop Pro 14, Ultrawide Monitor and 27in 4K Monitor.",
                               ["Laptop Pro 14", "Ultrawide Monitor", "27in 4K Monitor"])
    assert ok
    assert grade.grade_answer("Total revenue is about $123,450.", [123400.0])[0]  # within 3%
    bad, detail = grade.grade_answer("It is Germany.", ["US", 99])
    assert not bad and any(not d["ok"] for d in detail)
