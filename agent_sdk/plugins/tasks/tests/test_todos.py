"""Unit — the rail (parse/order) + the one `todos` tool (manage), in isolation."""

from __future__ import annotations

from agent_sdk.plugins.tasks.todos import TodoRail, TodosToolRuntime


def test_rail_deps_ready_and_topo_order():
    rail = TodoRail()
    rail.add("A")
    rail.add("C", deps=["t1"])      # depends on B
    rail.add("B", deps=["t0"])      # depends on A
    # ready() = only deps-satisfied open todos; topo_order() = dependency order
    assert [t.id for t in rail.ready()] == ["t0"]
    assert [t.title for t in rail.topo_order()] == ["A", "B", "C"]


def test_as_items_carries_spec_overrides():
    rail = TodoRail()
    rail.add("query", spec={"tools": ["db.query"], "system_prompt": "SQL only"})
    item = rail.as_items()[0]
    assert item["id"] == "t0" and item["input"] == "query"
    assert item["tools"] == ["db.query"] and item["system_prompt"] == "SQL only"


def _rt():
    rail = TodoRail()
    return rail, TodosToolRuntime(rail)


async def test_one_tool_with_actions():
    _, rt = _rt()
    specs = rt.get_tool_specs()
    assert len(specs) == 1 and specs[0]["name"] == "todos"  # ONE tool, not four
    assert {"add", "add_many", "list", "done", "block", "request_human"} <= set(
        specs[0]["input_schema"]["properties"]["action"]["enum"])


async def test_add_then_advance_to_complete():
    rail, rt = _rt()
    await rt.call_tool("todos", {"action": "add", "title": "A"}, [], set())
    await rt.call_tool("todos", {"action": "add", "title": "B"}, [], set())
    await rt.call_tool("todos", {"action": "done", "result": "did A"}, [], set())  # no id → next open
    assert rail.by_id("t0").status == "done" and rail.by_id("t0").result == "did A"
    out = await rt.call_tool("todos", {"action": "done", "id": "t1"}, [], set())
    assert rail.is_complete() and "complete" in out


async def test_dependency_order_enforced_by_tool():
    rail, rt = _rt()
    await rt.call_tool("todos", {"action": "add", "title": "A"}, [], set())
    await rt.call_tool("todos", {"action": "add_many", "steps": [{"title": "B", "deps": ["t0"]}]}, [], set())
    assert "needs t0" in await rt.call_tool("todos", {"action": "done", "id": "t1"}, [], set())


async def test_request_human_blocks():
    rail, rt = _rt()
    await rt.call_tool("todos", {"action": "add", "title": "needs sign-off"}, [], set())
    out = await rt.call_tool("todos", {"action": "request_human", "question": "approve?"}, [], set())
    assert "Escalated" in out and rail.by_id("t0").status == "blocked" and rt.human_asks == ["approve?"]
