"""Citation backfill — a paraphrased grounded answer cites its source even when
the model omitted the [chunk_id] marker; refusals/chitchat get NO backfill."""

from __future__ import annotations

from agent_sdk.contracts.memo import Citation
from agent_sdk.plugins.rag.citation import backfill_citations as _backfill_citations
from agent_sdk.plugins.rag.citation import citations_from_text as _citations_from_text

_CHUNKS = [
    {
        "chunk_id": "c1",
        "source_ref": "Quy chế Đào tạo cho SE.pdf",
        "score": 0.9,
        "text": "Điều kiện tốt nghiệp chương trình SE2019 yêu cầu hoàn thành đầy đủ các môn "
        "học bắt buộc và tích lũy đủ tín chỉ theo quy định đào tạo.",
        # Structural metadata rides from the retriever's _hits_result shim →
        # collect_citations → evidence channel → here. Optional + ignored
        # when absent (older payloads, non-paginated formats).
        "page_number": 5,
        "metadata": {"heading_tree": ["Chương III", "Điều 5"]},
    },
    {
        "chunk_id": "c2",
        "source_ref": "Quy định chấm bài thi.docx",
        "score": 0.6,
        "text": "Điểm cuối cùng của môn là điểm của đợt thi cuối cùng học viên tham gia.",
    },
]


def test_backfill_cites_paraphrased_answer():
    # answer paraphrases c1 (no [chunk_id] marker) → c1 backfilled by overlap
    answer = (
        "Để tốt nghiệp chương trình SE2019 bạn cần hoàn thành đầy đủ các môn học "
        "bắt buộc và tích lũy đủ tín chỉ theo quy định đào tạo của FUNiX."
    )
    out = _backfill_citations(answer, _CHUNKS, existing=[])
    assert any(c.chunk_id == "c1" for c in out)
    assert all(isinstance(c, Citation) for c in out)
    assert out[0].source_ref == "Quy chế Đào tạo cho SE.pdf"


def test_no_backfill_on_refusal():
    refusal = "Rất tiếc, mình chưa tìm thấy thông tin này trong tài liệu FUNiX."
    assert _backfill_citations(refusal, _CHUNKS, existing=[]) == []


def test_no_backfill_on_chitchat_no_overlap():
    chitchat = (
        "Chào bạn! Mình là trợ lý học tập của FUNiX, rất vui được hỗ trợ bạn hôm nay. "
        "Bạn cứ thoải mái đặt câu hỏi nhé."
    )
    assert _backfill_citations(chitchat, _CHUNKS, existing=[]) == []


def test_backfill_skips_already_cited():
    answer = (
        "Điều kiện tốt nghiệp SE2019: hoàn thành các môn học bắt buộc và tích lũy đủ "
        "tín chỉ theo quy định đào tạo. [c1]"
    )
    existing = _citations_from_text(answer, _CHUNKS)  # already cites c1
    out = _backfill_citations(answer, _CHUNKS, existing=existing)
    assert all(c.chunk_id != "c1" for c in out)  # not duplicated


def test_backfill_capped():
    many = [
        {
            "chunk_id": f"c{i}",
            "source_ref": f"d{i}",
            "score": 1.0 - i * 0.01,
            "text": "hoàn thành các môn học bắt buộc tích lũy tín chỉ quy định đào tạo",
        }
        for i in range(10)
    ]
    answer = "Cần hoàn thành các môn học bắt buộc và tích lũy tín chỉ theo quy định đào tạo."
    out = _backfill_citations(answer, many, existing=[])
    assert len(out) <= 6


def test_backfill_propagates_page_number_and_metadata():
    """The backfill path must surface structural metadata on the Citation
    it emits, so the user-facing footer can render ', p.5' / '§Điều 5'
    without a second lookup."""
    answer = (
        "Để tốt nghiệp chương trình SE2019 bạn cần hoàn thành đầy đủ các môn học "
        "bắt buộc và tích lũy đủ tín chỉ theo quy định đào tạo của FUNiX."
    )
    out = _backfill_citations(answer, _CHUNKS, existing=[])
    c1 = next(c for c in out if c.chunk_id == "c1")
    assert c1.page_number == 5
    assert c1.metadata == {"heading_tree": ["Chương III", "Điều 5"]}


def test_citations_from_text_propagates_page_number_and_metadata():
    """Marker-driven path (model emitted [c1]) must carry the same
    structural fields so the engine's finalize step renders them
    consistently regardless of which extraction branch produced the
    Citation."""
    answer = (
        "Điều kiện tốt nghiệp SE2019: hoàn thành các môn học bắt buộc và tích lũy đủ "
        "tín chỉ theo quy định đào tạo. [c1]"
    )
    out = _citations_from_text(answer, _CHUNKS)
    assert len(out) == 1
    assert out[0].page_number == 5
    assert out[0].metadata == {"heading_tree": ["Chương III", "Điều 5"]}
