"""Typed events, AgentStream, results."""

from __future__ import annotations

from agent_sdk.clients.messages import ProviderUsage
from agent_sdk.contracts.memo import Citation
from agent_sdk.events import (
    AgentStream,
    CitationFound,
    Final,
    RunStart,
    TextDelta,
    ToolCall,
    stamp,
)
from agent_sdk.result import AgentResult, Refusal, Trace, Usage


def test_event_positional_match():
    ev = TextDelta("hello")
    match ev:
        case TextDelta(text):
            assert text == "hello"
        case _:
            raise AssertionError


def test_event_to_json():
    ev = ToolCall(id="1", name="search", input={"q": "x"})
    j = ev.to_json()
    assert j["type"] == "tool_call"
    assert j["name"] == "search"
    assert j["input"] == {"q": "x"}


def test_citation_event_serializes_pydantic():
    cit = Citation(chunk_id="c1", source_ref="doc#1", supporting_span=(0, 5))
    ev = CitationFound(citation=cit)
    j = ev.to_json()
    assert j["citation"]["chunk_id"] == "c1"


def test_stamp_fills_trace_id_and_ts():
    ev = stamp(RunStart(), "trace-9")
    assert ev.trace_id == "trace-9"
    assert ev.ts > 0


async def test_agent_stream_iterates_and_awaits():
    result = AgentResult(text="done")

    async def source():
        yield RunStart(trace_id="t")
        yield TextDelta("partial")
        yield Final(result=result, trace_id="t")

    stream = AgentStream(source())
    seen = [type(ev).__name__ async for ev in stream]
    assert seen == ["RunStart", "TextDelta", "Final"]
    assert (await stream.result()).text == "done"


async def test_agent_stream_await_drains():
    result = AgentResult(text="answer")

    async def source():
        yield TextDelta("a")
        yield Final(result=result)

    stream = AgentStream(source())
    got = await stream
    assert got.text == "answer"


async def test_agent_stream_text_stream():
    async def source():
        yield TextDelta("foo")
        yield ToolCall(name="t")
        yield TextDelta("bar")
        yield Final(result=AgentResult(text="foobar"))

    stream = AgentStream(source())
    chunks = [c async for c in stream.text_stream]
    assert chunks == ["foo", "bar"]


def test_usage_from_provider_and_cost():
    u = Usage.from_provider(ProviderUsage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert u.input_tokens == 1_000_000
    assert u.estimated_cost > 0


def test_agent_result_str_and_json():
    r = AgentResult(
        text="hi", citations=[Citation(chunk_id="c", source_ref="s", supporting_span=(0, 1))]
    )
    assert str(r) == "hi"
    j = r.to_json()
    assert j["status"] == "answered"
    assert j["citations"][0]["chunk_id"] == "c"


def test_refused_result():
    r = AgentResult(
        text="", status="refused", refusal=Refusal(reason="no_citations", message="cannot confirm")
    )
    assert r.to_json()["refusal"]["reason"] == "no_citations"


def test_trace_timeline():
    t = Trace(
        flow_stages=[
            {"stage": "synthesize", "steps": [{"kind": "answer", "text": "x"}]},
        ]
    )
    tl = t.timeline()
    assert tl[0]["kind"] == "stage_start"
    assert tl[1]["kind"] == "answer"
    assert tl[-1]["kind"] == "stage_end"
