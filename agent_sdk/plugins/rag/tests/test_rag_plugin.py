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
from agent_sdk.plugins.rag.citation import (
    renumber_citation_markers,
    strip_citation_markers,
)
from agent_sdk.contracts.memo import Citation


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


def test_finalize_extracts_marker_and_renumbers():
    answer, cites, refusal = _finalize_grounding(
        f"Deploy is on Friday [{_CID}].", [], _CHUNKS, grounds=True, require_citations=True
    )
    assert refusal is None
    assert [c.chunk_id for c in cites] == [_CID]
    assert f"[{_CID}]" not in answer        # raw marker gone
    assert answer == "Deploy is on Friday [1]."  # renumbered to a footer-aligned ref


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


# ── renumbering (the platform standard [N] citation format) ──────────────────
def _cite(chunk_id, source_ref):
    return Citation(chunk_id=chunk_id, source_ref=source_ref, supporting_span=(0, 0))


def test_renumber_kg_node_refs():
    # The reported regression: raw KG node refs ([doc:…#pN], [ent:…]) leaked
    # because the legacy hex-only pattern never matched them. They must become [N].
    cites = [_cite("doc:quy-che-se#p173", "Quy chế SE"),
             _cite("ent:lms", "Hướng dẫn")]
    out = renumber_citation_markers(
        "Ask Mentor là hỏi đáp 1-1 [doc:quy-che-se#p173]. LMS [ent:lms].", cites)
    assert out == "Ask Mentor là hỏi đáp 1-1 [1]. LMS [2]."
    assert "[doc:" not in out and "[ent:" not in out


def test_renumber_dedups_by_document():
    # Two chunks of ONE document share a footer number.
    cites = [_cite("doc:se#p1", "Quy chế SE"), _cite("doc:se#p2", "Quy chế SE")]
    out = renumber_citation_markers("A [doc:se#p1] và B [doc:se#p2].", cites)
    assert out == "A [1] và B [1]."


def test_renumber_comma_list_and_unresolved():
    cites = [_cite("doc:a#p1", "Doc A"), _cite("doc:b#p1", "Doc B")]
    # comma-list expands; an unresolved ref is dropped (never leaked).
    out = renumber_citation_markers("X [doc:a#p1, doc:b#p1] Y [doc:ghost#p9].", cites)
    assert out == "X [1][2] Y."


def test_renumber_leaves_ordinary_brackets():
    out = renumber_citation_markers("see [1] and the year [2025]", [])
    assert out == "see [1] and the year [2025]"


def test_renumber_collapses_adjacent_duplicates():
    # The reported scenario: two markers to the SAME document side by side.
    cites = [_cite("doc:se#p173", "Quy chế SE"), _cite("doc:se#p46", "Quy chế SE")]
    out = renumber_citation_markers("Mentor [doc:se#p173] [doc:se#p46] hỗ trợ.", cites)
    assert out == "Mentor [1] hỗ trợ."  # [1] [1] → [1]
    # but distinct, non-adjacent refs are preserved
    cites2 = [_cite("doc:a#p1", "A"), _cite("doc:b#p1", "B")]
    out2 = renumber_citation_markers("X [doc:a#p1] giữa Y [doc:b#p1].", cites2)
    assert out2 == "X [1] giữa Y [2]."
