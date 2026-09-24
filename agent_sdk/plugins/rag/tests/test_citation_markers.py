"""A model cites the source it was shown — by its id, or by the ``src`` path it reads.

Wiki evidence is shown as ``[wiki:<uuid>:<path>] (src=<path>) …``. Models copy the
readable ``[<path>]``; the marker then resolved to nothing, was dropped from the prose,
and the paraphrase backfill guessed the citations from word overlap instead — citing
every page in the evidence channel (seen on the KOMU bot, six sources for one).
"""

from __future__ import annotations

from agent_sdk.plugins.rag import _finalize_grounding
from agent_sdk.plugins.rag.citation import citations_from_text, renumber_citation_markers

WIKI = "58e5a86a-83f6-40e5-88d5-2f321e0c9c0c"


def _chunk(path: str, text: str) -> dict:
    return {"chunk_id": f"wiki:{WIKI}:{path}", "source_ref": path, "text": text, "score": 0.5}


CHUNKS = [
    _chunk(
        "policy/06-quy-dinh.md",
        "Kể từ ngày 15/01/2026 công ty chính thức ngừng áp dụng chế độ đi muộn về sớm",
    ),
    _chunk("policy/07-pm-rules.md", "PM reject request đi muộn về sớm nhiều lần trong tuần"),
    _chunk("KOMU-BOT.md", "Komu là trợ lý chính sách nội bộ của công ty, trả lời chính sách"),
]


def test_a_marker_naming_the_source_path_cites_that_chunk():
    answer = "Từ 15/01/2026 công ty ngừng áp dụng chế độ đi muộn [policy/06-quy-dinh.md]."

    cited = citations_from_text(answer, CHUNKS)

    assert [c.chunk_id for c in cited] == [f"wiki:{WIKI}:policy/06-quy-dinh.md"]


def test_a_full_wiki_id_marker_is_renumbered_not_leaked():
    answer = f"Công ty ngừng áp dụng chế độ đi muộn [wiki:{WIKI}:policy/06-quy-dinh.md]."
    cited = citations_from_text(answer, CHUNKS)

    assert renumber_citation_markers(answer, cited) == "Công ty ngừng áp dụng chế độ đi muộn [1]."


def test_a_path_marker_is_renumbered_like_an_id_marker():
    answer = "A [policy/06-quy-dinh.md]. B [policy/07-pm-rules.md]. C [policy/06-quy-dinh.md]."
    cited = citations_from_text(answer, CHUNKS)

    assert renumber_citation_markers(answer, cited) == "A [1]. B [2]. C [1]."


def test_marked_sources_are_the_citations_and_backfill_adds_no_others():
    answer = (
        "Kể từ ngày 15/01/2026, công ty chính thức ngừng áp dụng chế độ đi muộn về sớm "
        "cho toàn thể nhân viên trong công ty [policy/06-quy-dinh.md]."
    )

    clean, cited, refusal = _finalize_grounding(answer, [], CHUNKS, True, True)

    assert [c.source_ref for c in cited] == ["policy/06-quy-dinh.md"]
    assert clean.endswith("nhân viên trong công ty [1].")
    assert refusal is None


def test_an_ordinary_bracket_is_not_taken_for_a_source():
    answer = "Xem mục [1] và năm [2026] trong chính sách."

    assert citations_from_text(answer, CHUNKS) == []
    assert renumber_citation_markers(answer, []) == answer
