"""Probe engine + single-HTML report renderer."""

from __future__ import annotations

from agent_sdk import (
    Harness,
    PreactAgent,
    ProbeRecord,
    Scenario,
    probe,
    render_html,
    tool,
    write_html,
)
from agent_sdk.clients import FakeClient


def _agent(script, **kw):
    return PreactAgent(client=FakeClient(script), instructions="helpful", **kw)


async def test_probe_captures_internals():
    rec = await probe(_agent(["the answer"]), "what is up?", label="t1")
    assert isinstance(rec, ProbeRecord)
    assert rec.status == "answered"
    assert rec.answer == "the answer"
    assert rec.flow in ("qna", "research", "clarify")
    assert any(lb["id"] == "synthesize" for lb in rec.lobes)
    assert "synthesize" in rec.activated_lobes


async def test_probe_records_tool_calls():
    @tool
    async def search(q: str) -> str:
        return "found"

    agent = PreactAgent(
        client=FakeClient([{"tools": [{"name": "search", "input": {"q": "x"}}]}, "done"]),
        instructions="bot",
        tools=[search],
        flows=[__import__("agent_sdk").flow("qna", stages=["synthesize"], signal={"const": 1.0})],
        stages=[__import__("agent_sdk").stage("synthesize", lobes=["synthesize"], loop="agentic", tools=["search"])],
    )
    rec = await probe(agent, "go", label="tool turn")
    assert rec.tool_calls[0]["name"] == "search"
    assert rec.tool_calls[0]["output"] == "found"


async def test_render_html_combines_report_and_probes():
    agent = _agent(["answer"])
    report = await Harness(agent).run([
        Scenario(input="compare a and b in extensive detail right now", expect_path="research"),
        Scenario(input="hi?", expect_path="qna"),
    ])
    rec = await probe(agent, "compare a and b in extensive detail", label="probe1")
    html = render_html("coding-agent-bench", report=report, probes=[rec], generated_at="FIXED")

    assert html.startswith("<!doctype html>")
    assert "coding-agent-bench" in html
    assert "Scenarios" in html and "Probes" in html
    assert "research" in html  # routed flow shown
    assert "probe1" in html
    # enriched viewer functions are present (kept in the preferred layout)
    assert "signals" in html and "edges" in html  # lobe OY detail
    assert "raw JSON" in html  # drilldown
    assert "lobe activation" in html
    # self-contained: no external asset references
    assert "http://" not in html and "src=" not in html


async def test_probe_carries_skill_selection():
    import agent_sdk as sdk

    sk = sdk.Skill("kbk", when="look things up", disclosure="on_demand", stages=["work"])
    agent = PreactAgent(
        client=FakeClient(["done"]),
        instructions="bot",
        universal_memory=False,
        skills=[sk],
        flows=[sdk.flow("work", stages=["work"], signal={"const": 1.0})],
        stages=[sdk.stage("work", lobes=["synthesize"], loop="single")],
    )
    rec = await probe(agent, "go", label="skill turn")
    assert isinstance(rec.skill_selection, list)
    assert any(
        r.get("label") == "kbk"
        for sel in rec.skill_selection
        for r in sel.get("ranking", [])
    )
    assert isinstance(rec.tool_selection, list)
    assert isinstance(rec.degraded, list)


async def test_probe_carries_path_and_hints():
    rec = await probe(_agent(["x"]), "hi?", label="t")
    assert rec.path.get("name") in ("qna", "research", "clarify", "relational", "emergent")
    assert isinstance(rec.hints, list)  # optimization hotspots (may be empty)


async def test_write_html(tmp_path):
    agent = _agent(["answer"])
    rec = await probe(agent, "hello?", label="p")
    out = write_html(tmp_path / "r.html", "bench", probes=[rec])
    assert out.exists()
    assert "<html>" in out.read_text()


async def test_render_html_combines_overview_and_probes():
    # one HTML carrying BOTH the verdict/group overview and the rich probe inspect
    agent = _agent(["answer"])
    rec = await probe(agent, "compare a and b", label="probe1")
    verdict = {"status": "READY", "reasons": [], "metrics": {"activation.recall": 1.0}}
    modes = {"activation": {"checks": [{"id": "activation.code_review", "ok": True,
                                        "detail": "P=1.0 R=1.0"}], "n": 1, "pass": 1,
                            "all_pass": True}}
    html = render_html("skillbench", verdict=verdict, modes=modes, probes=[rec],
                       generated_at="FIXED")
    # overview half
    assert "Overview" in html and 'class="verdict READY"' in html
    assert "activation.code_review" in html and "activation.recall" in html
    # probe-inspect half (rich timeline + drilldown)
    assert "Probes" in html and "probe1" in html and "raw JSON" in html
    assert "http://" not in html and "src=" not in html  # self-contained


async def test_render_html_report_only():
    agent = _agent(["x"])
    report = await Harness(agent).run([Scenario(input="hi?", expect_path="qna")])
    html = render_html("t", report=report, generated_at="FIXED")
    assert "Scenarios" in html
    assert "Probes" not in html  # no probes section when none given
