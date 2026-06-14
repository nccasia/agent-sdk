"""Adapting SDK probes into the reused benchmark viewer.html."""

from __future__ import annotations

from agent_sdk import (
    Flows,
    Lobes,
    PreactAgent,
    Stages,
    flow,
    probe,
    render_viewer_html,
    stage,
    to_viewer_record,
    tool,
)
from agent_sdk.clients import FakeClient


async def test_engine_captures_llm_calls():
    @tool
    async def search(q: str) -> str:
        return "hit"

    agent = PreactAgent(
        client=FakeClient([{"tools": [{"name": "search", "input": {"q": "x"}}]}, "done"]),
        instructions="bot",
        tools=[search],
        flows=[flow("qna", stages=["synthesize"], signal={"const": 1.0})],
        stages=[stage("synthesize", lobes=["synthesize"], loop="agentic", tools=["search"])],
    )
    result = await agent.query("go")
    calls = result.trace.llm_calls
    # one hop with a tool_use response + a tool_result, one final answer hop
    assert calls[0]["stage"] == "synthesize"
    assert any(b["type"] == "tool_use" for b in calls[0]["response"])
    assert calls[0]["tool_results"][0]["name"] == "search"
    assert calls[-1]["stop_reason"] == "end_turn"


async def test_engine_captures_system_prompt_for_prompt_panel():
    agent = PreactAgent(client=FakeClient(["answer"]), instructions="You are helpful.")
    result = await agent.query("what?")
    fs = result.trace.flow_stages[0]
    # the composed system prompt is recorded on the stage AND the model call
    assert "You are helpful." in fs["system_prompt"]
    call = result.trace.llm_calls[0]
    assert "You are helpful." in call["system"]
    assert call["messages"]  # real bytes sent
    assert call["model"] is not None
    # the viewer adapter surfaces it on flow_steps (what the Prompt panel reads)
    rec = await probe(agent, "again?")
    vr = to_viewer_record(rec)
    assert vr["trace"]["flow_steps"][0]["system_prompt"]


async def test_prompt_provenance_segments_colour_by_lobe():
    agent = PreactAgent(
        client=FakeClient(["answer"]),
        instructions="You are helpful.",
        lobes=Lobes.minimal(),
        stages=Stages.minimal(),
        flows=Flows.minimal(),
    )
    result = await agent.query("what?")
    fs = result.trace.flow_stages[0]
    segs = fs["system_segments"]
    txt = fs["system_prompt"]
    sources = [s["source"] for s in segs]
    # provenance is tagged by lobe id (the default qna synthesize slice) + sections
    assert "instructions" in sources and "datetime" in sources
    assert "synthesize" in sources  # a real lobe id → coloured by lobe
    # every segment maps to actual text and they are ordered/non-overlapping
    last = 0
    for s in segs:
        assert s["start"] >= last and txt[s["start"] : s["end"]]
        last = s["end"]
    # the viewer adapter passes segments through
    vr = to_viewer_record(await probe(agent, "again?"))
    assert vr["trace"]["flow_steps"][0]["system_segments"]


async def test_llm_calls_carry_real_tool_defs():
    @tool
    async def search(query: str, top_k: int = 5) -> str:
        "Search the KB."
        return "x"

    agent = PreactAgent(
        client=FakeClient([{"tools": [{"name": "search", "input": {"query": "a"}}]}, "done"]),
        instructions="b",
        tools=[search],
        flows=[flow("qna", stages=["synthesize"], signal={"const": 1.0})],
        stages=[stage("synthesize", lobes=["synthesize"], loop="agentic", tools=["search"])],
    )
    result = await agent.query("go")
    tools = result.trace.llm_calls[0]["tools"]  # the real `tools` payload that hop
    assert tools[0]["name"] == "search"
    assert tools[0]["description"] == "Search the KB."
    params = {p["name"]: p["required"] for p in tools[0]["params"]}
    assert params == {"query": True, "top_k": False}


async def test_tools_in_prompt_section():
    @tool
    async def search(query: str, top_k: int = 5) -> str:
        "Search the KB."
        return "x"

    agent = PreactAgent(
        client=FakeClient(["done"]),
        instructions="b",
        tools=[search],
        tools_in_prompt=True,
        flows=[flow("qna", stages=["synthesize"], signal={"const": 1.0})],
        stages=[stage("synthesize", lobes=["synthesize"], loop="agentic", tools=["search"])],
    )
    result = await agent.query("go")
    fs = result.trace.flow_stages[0]
    sources = [s["source"] for s in fs["system_segments"]]
    assert "tools" in sources  # a colored `tools` provenance section, inline
    tools_seg = next(s for s in fs["system_segments"] if s["source"] == "tools")
    block = fs["system_prompt"][tools_seg["start"] : tools_seg["end"]]
    assert "search(query*, top_k)" in block  # name + params (required = *)
    assert "Search the KB." in block


async def test_tools_in_prompt_off_by_default():
    @tool
    async def search(query: str) -> str:
        return "x"

    agent = PreactAgent(
        client=FakeClient(["done"]),
        instructions="b",
        tools=[search],
        flows=[flow("qna", stages=["synthesize"], signal={"const": 1.0})],
        stages=[stage("synthesize", lobes=["synthesize"], loop="agentic", tools=["search"])],
    )
    result = await agent.query("go")
    sources = [s["source"] for s in result.trace.flow_stages[0]["system_segments"]]
    assert "tools" not in sources  # default: tools only via the native param


async def test_to_viewer_record_schema():
    rec = await probe(
        PreactAgent(client=FakeClient(["the answer"]), instructions="bot"),
        "what is up?",
        label="t1",
    )
    vr = to_viewer_record(rec)
    assert vr["label"] == "t1"
    t = vr["trace"]
    # the shapes the viewer reads
    assert "name" in t["path"]
    assert isinstance(t["lobes"], list) and t["lobes"]
    assert isinstance(t["llm_calls"], list)
    assert t["flow_steps"] and "step" in t["flow_steps"][0]
    assert "input_tokens" in t["usage_rollup"]


async def test_render_viewer_html_injects_records():
    rec = await probe(PreactAgent(client=FakeClient(["x"]), instructions="b"), "hi?", label="r1")
    html = render_viewer_html([rec], label="bench")
    # the template's data seam is filled, not left as the placeholder
    assert "<!--TRACE_DATA-->" not in html
    assert 'id="trace-data"' in html
    assert "r1" in html
    # the real viewer chrome is present (reused, not a re-implementation)
    assert "trace-data" in html and "drop" in html.lower()
