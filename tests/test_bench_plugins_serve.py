"""Bench harness, plugins, and the serving worker."""

from __future__ import annotations

import pytest

from agent_sdk import PreactAgent, tool
from agent_sdk.bench import Harness, Scenario
from agent_sdk.clients import FakeClient
from agent_sdk.plugins import (
    GuardrailError,
    PluginGuardrails,
    PluginMCP,
    PluginOTel,
    PluginWorkspace,
    VirtualWorkspace,
)
from agent_sdk.serve import (
    AgentWorker,
    InProcessEventSink,
    InProcessQueue,
    Job,
)


def _agent(**kw):
    return PreactAgent(client=FakeClient(["answer"]), instructions="helpful", **kw)


# ── bench ────────────────────────────────────────────────────────────────────
async def test_harness_routing_assertions():
    agent = _agent()
    report = await Harness(agent).run(
        [
            Scenario(
                input="compare A and B thoroughly across many dimensions now",
                expect_path="research",
            ),
            Scenario(input="what does the build script do", expect_path="qna"),
        ]
    )
    summary = report.summary()
    assert summary["path_accuracy"] == 1.0
    assert summary["scenarios"] == 2


async def test_harness_detects_mismatch():
    agent = _agent()
    report = await Harness(agent).run([Scenario(input="hi?", expect_path="research")])
    assert report.summary()["passed"] == 0
    assert report.results[0].failures


async def test_harness_run_llm_status():
    agent = _agent()
    report = await Harness(agent).run(
        [Scenario(input="q?", run_llm=True, expect_status="answered")]
    )
    assert report.results[0].status == "answered"


# ── plugins ──────────────────────────────────────────────────────────────────
async def test_plugin_workspace_fs_tools():
    agent = PreactAgent(
        client=FakeClient(
            [
                {
                    "tools": [
                        {"name": "fs.write", "input": {"path": "notes.md", "content": "hello"}}
                    ]
                },
                "Wrote the file.",
            ]
        ),
        instructions="writer",
        plugins=[PluginWorkspace(driver="virtual")],
        flows=[__import__("agent_sdk").flow("qna", stages=["synthesize"], signal={"const": 1.0})],
        stages=[
            __import__("agent_sdk").stage(
                "synthesize", lobes=["synthesize"], loop="agentic", tools=["fs.write"]
            )
        ],
    )
    result = await agent.query("write notes")
    assert "Wrote" in result.text
    content = await agent.workspace.read("notes.md")
    assert content == b"hello"


def test_plugin_workspace_local(tmp_path):
    plugin = PluginWorkspace(driver="local", root=str(tmp_path))
    assert plugin.workspace.root == str(tmp_path)


async def test_virtual_workspace_ops():
    ws = VirtualWorkspace()
    await ws.write("a.txt", b"one")
    assert await ws.read("a.txt") == b"one"
    await ws.edit("a.txt", " two")
    assert await ws.read("a.txt") == b"one two"
    assert await ws.list() == ["a.txt"]


async def test_plugin_mcp_injected_tools():
    @tool
    async def weather(city: str) -> str:
        return f"sunny in {city}"

    agent = PreactAgent(
        client=FakeClient(
            [
                {"tools": [{"name": "weather", "input": {"city": "Hanoi"}}]},
                "It's sunny.",
            ]
        ),
        instructions="assistant",
        plugins=[PluginMCP(tools=[weather])],
        flows=[__import__("agent_sdk").flow("qna", stages=["synthesize"], signal={"const": 1.0})],
        stages=[
            __import__("agent_sdk").stage(
                "synthesize", lobes=["synthesize"], loop="agentic", tools=["weather"]
            )
        ],
    )
    result = await agent.query("weather?")
    assert "sunny" in result.text


async def test_plugin_otel_records_events():
    otel = PluginOTel()
    agent = _agent(plugins=[otel])
    await agent.query("q?")
    assert "run_start" in otel.events
    assert "final" in otel.events


async def test_plugin_guardrails_pre_blocks():
    def no_secrets(text: str) -> None:
        if "password" in text.lower():
            raise GuardrailError("blocked")

    agent = _agent(plugins=[PluginGuardrails(pre=[no_secrets])])
    with pytest.raises(GuardrailError):
        await agent.query("my password is 123")


async def test_plugin_guardrails_post_runs():
    seen = {}

    def record(result) -> None:
        seen["status"] = result.status

    agent = _agent(plugins=[PluginGuardrails(post=[record])])
    await agent.query("q?")
    assert seen["status"] == "answered"


# ── serve ────────────────────────────────────────────────────────────────────
async def test_worker_drains_queue_and_publishes():
    agent = PreactAgent(client=FakeClient(["served answer", "served answer"]), instructions="x")
    queue = InProcessQueue()
    sink = InProcessEventSink()
    worker = AgentWorker(agent, queue=queue, sink=sink, concurrency=2)

    job = Job(input="question?")
    await queue.enqueue(job)

    # collect events for this job, then run the worker for exactly one job
    collected = []

    async def collect():
        async for ev in sink.subscribe(job.trace_id):
            collected.append(type(ev).__name__)

    import asyncio

    sub = asyncio.ensure_future(collect())
    await worker.serve(max_jobs=1)
    await sub
    assert "Final" in collected


async def test_session_lock_serializes_same_conversation():
    from agent_sdk.serve import InProcessLock

    lock = InProcessLock()
    a = lock("conv-1")
    b = lock("conv-1")
    assert a is b  # same key → same lock
    assert lock("conv-2") is not a
