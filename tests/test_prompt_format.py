"""The system prompt is composed as XML by default (Claude-Code-style).

Claude parses XML-delimited context more reliably than flat markdown, so each composed section
is wrapped in a tag. ``prompt_format="markdown"`` opts back out. Provenance segment sources are
unchanged (the viewer still colours by source), only the rendered text gains tags.
"""

from __future__ import annotations

from agent_sdk import PreactAgent
from agent_sdk.clients.fake import FakeClient
from agent_sdk.session import SessionState


def _system(agent, query: str = "hello") -> str:
    eng = agent.engine
    return eng._compose_system(eng.stage_registry.stages()[0], {"query": query}, SessionState(), [])


def test_xml_is_the_default():
    a = PreactAgent(client=FakeClient(), instructions="You are helpful.")
    assert a.engine.prompt_format == "xml"
    sys = _system(a)
    assert "<instructions>" in sys and "</instructions>" in sys
    assert "You are helpful." in sys
    assert "<env>" in sys  # datetime → <env>, Claude-Code canonical tag


def test_markdown_opt_out():
    a = PreactAgent(client=FakeClient(), instructions="You are helpful.", prompt_format="markdown")
    sys = _system(a)
    assert "<instructions>" not in sys
    assert "You are helpful." in sys  # content unchanged, just untagged


def test_segments_keep_provenance_sources_under_xml():
    a = PreactAgent(client=FakeClient(), instructions="X")
    eng = a.engine
    _, segs = eng._compose_system_segmented(
        eng.stage_registry.stages()[0], {"query": "hi"}, SessionState(), []
    )
    sources = {s["source"] for s in segs}
    # tag may differ (datetime→env) but the segment source is the stable provenance name
    assert "instructions" in sources
    assert "datetime" in sources
