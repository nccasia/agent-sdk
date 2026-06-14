"""@tool introspection + FunctionToolRuntime."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from agent_sdk.tools import FunctionToolRuntime, Tool, tool


class Ticket(BaseModel):
    title: str
    priority: int = 3


def test_bare_decorator_schema_from_signature():
    @tool
    async def search(query: str, top_k: int = 5) -> str:
        "Search the knowledge base."

    assert isinstance(search, Tool)
    spec = search.spec
    assert spec["name"] == "search"
    assert spec["description"] == "Search the knowledge base."
    props = spec["input_schema"]["properties"]
    assert props["query"] == {"type": "string"}
    assert props["top_k"]["type"] == "integer"
    assert props["top_k"]["default"] == 5
    assert spec["input_schema"]["required"] == ["query"]


def test_explicit_name_and_requires():
    @tool(name="tickets.create", requires=["acl"])
    async def create_ticket(title: str) -> str:
        return "ok"

    assert create_ticket.name == "tickets.create"
    assert create_ticket.requires == ("acl",)


def test_pydantic_arg_schema_and_validation():
    @tool
    async def create(args: Ticket) -> str:
        return f"{args.title}:{args.priority}"

    schema = create.input_schema
    assert schema["type"] == "object"
    assert "title" in schema["properties"]


async def test_invoke_async_and_sync():
    @tool
    async def a(x: int) -> int:
        return x + 1

    @tool
    def b(x: int) -> int:
        return x * 2

    assert await a.invoke({"x": 1}) == 2
    assert await b.invoke({"x": 3}) == 6


async def test_invoke_pydantic():
    @tool
    async def create(args: Ticket) -> str:
        return f"{args.title}:{args.priority}"

    assert await create.invoke({"title": "bug"}) == "bug:3"


async def test_runtime_specs_and_call():
    @tool
    async def search(query: str) -> str:
        return f"results for {query}"

    rt = FunctionToolRuntime([search])
    specs = rt.get_tool_specs()
    assert specs[0]["name"] == "search"
    out = await rt.call_tool("search", {"query": "x"}, [], set())
    assert out == "results for x"


async def test_runtime_unknown_tool():
    rt = FunctionToolRuntime([])
    out = await rt.call_tool("nope", {}, [], set())
    assert "unknown tool" in out


async def test_runtime_stringifies_non_str_return():
    @tool
    async def nums() -> list:
        return [1, 2, 3]

    rt = FunctionToolRuntime([nums])
    out = await rt.call_tool("nums", {}, [], set())
    assert out == "[1, 2, 3]"


async def test_runtime_tool_error_is_surfaced():
    @tool
    async def boom() -> str:
        raise RuntimeError("kaboom")

    rt = FunctionToolRuntime([boom])
    out = await rt.call_tool("boom", {}, [], set())
    assert "kaboom" in out


async def test_missing_required_arg_returns_clean_error():
    @tool(name="Write")
    async def write(file_path: str, content: str) -> str:
        return f"wrote {file_path}"

    rt = FunctionToolRuntime([write])
    out = await rt.call_tool("Write", {"content": "x"}, [], set())
    # model-actionable: names the missing arg, no Python qualname / traceback
    assert "requires argument" in out and "'file_path'" in out
    assert "positional argument" not in out
    assert "<locals>" not in out
    # a well-formed call still runs
    assert (
        await rt.call_tool("Write", {"file_path": "A.md", "content": "x"}, [], set())
        == "wrote A.md"
    )


def test_missing_required_lists_all_absent_args():
    @tool(name="Write")
    async def write(file_path: str, content: str) -> str:
        return "ok"

    assert write.missing_required({}) == ["file_path", "content"]
    assert write.missing_required({"file_path": "A"}) == ["content"]
    assert write.missing_required({"file_path": "A", "content": "x"}) == []


def test_missing_required_skips_pydantic_form():
    @tool
    async def create(args: Ticket) -> str:
        return "ok"

    # the pydantic model validates its own payload — guard defers to it
    assert create.missing_required({}) == []


def test_optional_annotation():
    @tool
    def f(x: Optional[str] = None) -> str:  # noqa: UP045
        return x or ""

    props = f.input_schema["properties"]
    assert props["x"]["type"] == "string"
    assert f.input_schema["required"] == []
