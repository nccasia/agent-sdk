"""RagPlugin owns grounding; a non-RAG agent has none.

Locks in the capability split: ``cite`` + citation extraction + ground-or-refuse
live in the opt-in :class:`RagPlugin` (or ``require_citations=True``), while the
default agent keeps output *safety* (``filter``) but no retrieval grounding.
"""

from __future__ import annotations

from agent_sdk import PreactAgent
from agent_sdk.clients.fake import FakeClient
from agent_sdk.plugins import RagPlugin, SafetyPlugin
from agent_sdk.plugins.rag import _finalize_grounding
from agent_sdk.plugins.rag.citation import strip_citation_markers


def _caps(agent: PreactAgent) -> set[str]:
    return {lb.id for lb in agent.engine.lobes}


def test_default_agent_has_safety_not_grounding():
    a = PreactAgent(client=FakeClient())
    ids = _caps(a)
    assert "filter" in ids          # safety: every agent
    assert "cite" not in ids        # grounding: opt-in only
    assert not a.engine._finalize_hooks
    assert not a.engine._tool_result_hooks


def test_rag_plugin_adds_cite_and_hooks():
    a = PreactAgent(client=FakeClient(), plugins=[RagPlugin()])
    assert "cite" in _caps(a)
    assert len(a.engine._finalize_hooks) == 1
    assert len(a.engine._tool_result_hooks) == 1


def test_require_citations_auto_enables_rag():
    a = PreactAgent(client=FakeClient(), require_citations=True)
    assert "cite" in _caps(a)
    assert len(a.engine._finalize_hooks) == 1


def test_safety_plugin_owns_filter_only():
    assert [lb.id for lb in SafetyPlugin().lobes()] == ["filter"]
    assert [lb.id for lb in RagPlugin().lobes()] == ["cite"]


# ── the finalize-grounding contract (pure) ──────────────────────────────────
# A realistic chunk_id (hex-ish) so the marker is both extracted AND stripped (the
# strip regex is conservative — it leaves short tokens like [c1]/[1] alone).
_CID = "a1b2c3d4"
_CHUNKS = [{"chunk_id": _CID, "source_ref": "doc#1", "score": 0.9,
            "text": "the deploy day is friday in the release window"}]


def test_finalize_extracts_marker_and_strips():
    answer, cites, refusal = _finalize_grounding(
        f"Deploy is on Friday [{_CID}].", [], _CHUNKS, grounds=True, require_citations=True
    )
    assert refusal is None
    assert [c.chunk_id for c in cites] == [_CID]
    assert f"[{_CID}]" not in answer  # marker stripped from user-facing text


def test_finalize_ground_or_refuse_when_no_citations():
    answer, cites, refusal = _finalize_grounding(
        "Some ungrounded claim.", [], [], grounds=True, require_citations=True
    )
    assert refusal == "no_citations"


def test_finalize_no_refusal_when_not_grounding():
    _, _, refusal = _finalize_grounding(
        "hi there", [], [], grounds=False, require_citations=True
    )
    assert refusal is None


def test_strip_leaves_ordinary_brackets():
    assert strip_citation_markers("see [1] and the year [2025]") == "see [1] and the year [2025]"
