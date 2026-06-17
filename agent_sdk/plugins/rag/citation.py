"""Citation extraction + grounding — the RAG plugin's logic, not the engine's.

These are the pure functions that turn a model answer + an evidence channel into
``Citation`` objects and a clean user-facing string. They were the engine core's
``_citations_from_text`` / ``_backfill_citations`` / ``_strip_citation_markers`` /
``_extract_citations``; moving them here keeps the SDK kernel capability-free —
the engine carries no notion of "a citation" beyond the generic ``Citation`` data
shape it returns on a result. The :class:`RagPlugin` wires these into the engine
via ``add_finalize_hook`` (extract + backfill + strip + ground-or-refuse) and
``add_tool_result_hook`` (a tool that emits ``{"citations": [...]}``).

Domain-free: no KB/provider/language assumptions — overlap scoring uses a generic
length filter, not a stopword list.
"""

from __future__ import annotations

import json
import re
import unicodedata

from agent_sdk.contracts.memo import Citation

__all__ = [
    "renumber_citation_markers",
    "strip_citation_markers",
    "citations_from_text",
    "backfill_citations",
    "extract_tool_citations",
]

# A single inline grounding-marker token the model emits. Three shapes:
#   • a KG node ref (v3.0.0+ knowledge-graph KB) — ``doc:<slug>#pN``, ``ent:lms``,
#     ``cell:<slug>#Sheet!B7`` — kind-prefixed, carrying letters / ':' / '#' / '-'.
#   • a legacy vector chunk id — a uuid or 8+ hex run.
#   • a golden case ref — ``golden:<case>``.
# The KG refs are why the old hex-only pattern leaked: ``[doc:…#p173]`` matched
# neither the uuid nor the hex branch, so raw refs survived into delivered answers.
_KG_REF = r"(?:doc|ent|cell|sec|para|link|tbl|row|page|sheet|toc|kb|attr|header):[^\]\s,]+"
_MARKER_TOKEN = rf"(?:golden:[^\],]+|{_KG_REF}|[0-9a-fA-F][0-9a-fA-F-]{{5,}})"
# A whole marker = one or more tokens in one bracket (``[id]`` or ``[id1, id2]``);
# group 1 is any leading whitespace so a renumber/strip can rebuild spacing.
_CITE_MARKER_RE = re.compile(
    rf"(\s*)\[\s*{_MARKER_TOKEN}(?:\s*,\s*{_MARKER_TOKEN})*\s*\]"
)


def _citation_numbering(citations: list[Citation]) -> dict[str, int]:
    """Map ``chunk_id`` → 1-based reference number, deduped by DOCUMENT
    (``source_ref``), in first-appearance order. This is the SAME order the
    delivery footer numbers its "Nguồn tham khảo" entries, so an inline ``[N]``
    lines up with footer entry ``N``. Multiple chunks of one document share a
    number (one footer line per document)."""
    doc_num: dict[str, int] = {}
    cid_num: dict[str, int] = {}
    for c in citations:
        cid = getattr(c, "chunk_id", "") or ""
        key = getattr(c, "source_ref", "") or cid
        if not key:
            continue
        if key not in doc_num:
            doc_num[key] = len(doc_num) + 1
        if cid:
            cid_num[cid] = doc_num[key]
    return cid_num


def renumber_citation_markers(text: str, citations: list[Citation]) -> str:
    """Rewrite inline machine markers into human reference numbers — the platform
    STANDARD citation format. Each ``[<chunk_id>]`` / ``[doc:…#pN]`` / ``[golden:…]``
    becomes a ``[N]`` whose number matches the source's position in the delivery
    footer (``Nguồn tham khảo`` / the portal citation panel), so the reader sees
    ``… Ask Mentor [1][2].`` and finds ``[1]``/``[2]`` with their links below.

    A marker whose id resolves to no citation is dropped (residual machine noise,
    never leaked). Ordinary brackets (``[1]``, ``[2025]``, markdown links) are
    left untouched. Comma-lists (``[id1, id2]``) expand to ``[N1][N2]``."""
    if not text or "[" not in text:
        return text
    cid_num = _citation_numbering(citations)

    def _sub(m: re.Match) -> str:
        lead = m.group(1)
        body = m.group(0)[len(lead):].strip()[1:-1]  # drop leading ws + the [ ]
        nums: list[int] = []
        for tok in body.split(","):
            n = cid_num.get(tok.strip())
            if n is not None and n not in nums:
                nums.append(n)
        if not nums:
            return ""  # unresolved marker → drop, never leak the raw ref
        return (" " if lead else "") + "".join(f"[{n}]" for n in nums)

    cleaned = _CITE_MARKER_RE.sub(_sub, text)
    # Collapse adjacent duplicate reference numbers ("[1] [1]" / "[1][1]" → "[1]")
    # — two markers to the same document in one spot read as one reference.
    cleaned = re.sub(r"(\[\d+\])(?:\s*\1)+", r"\1", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def strip_citation_markers(text: str) -> str:
    """Remove inline ``[chunk_id]`` / ``[doc:…#pN]`` / ``[golden:…]`` grounding
    markers entirely from the user-facing answer. Retained for callers that render
    citations purely out-of-band (no inline reference numbers); the default
    grounding contract now uses :func:`renumber_citation_markers`. Leaves ordinary
    brackets ([1], [2025], markdown links) untouched."""
    if not text or "[" not in text:
        return text
    cleaned = _CITE_MARKER_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def citations_from_text(text: str, chunks: list[dict]) -> list[Citation]:
    """Citations for a one-shot (single-loop) answer: each evidence chunk whose
    ``[chunk_id]`` literally appears in the answer becomes a Citation. Lets a
    one-shot RAG stage ground from a prefetch-seeded evidence channel, the way the
    agentic path grounds from tool-output citations. No tool loop required."""
    if not text or not chunks:
        return []
    out: list[Citation] = []
    seen: set[str] = set()
    for ch in chunks:
        cid = str(ch.get("chunk_id") or "")
        if cid and cid not in seen and f"[{cid}]" in text:
            seen.add(cid)
            out.append(Citation(
                chunk_id=cid,
                source_ref=str(ch.get("source_ref") or ""),
                supporting_span=(0, len(text)),
                # Propagate structural metadata from the evidence channel so the
                # user-facing citation footer can show ", p.N" / "§heading" when
                # the LLM or the delivery client renders it. Optional + ignored
                # when absent (older evidence payloads, non-paginated formats).
                page_number=ch.get("page_number"),
                metadata=dict(ch.get("metadata") or {}),
            ))
    return out


_BACKFILL_MIN_ANSWER_CHARS = 60   # a refusal/one-liner is shorter ⇒ never backfilled
_BACKFILL_MAX_ADD = 6             # cap added citations (wide enough to include the
                                  # expected doc when a relevant chunk ranks lower)
_BACKFILL_MIN_OVERLAP = 3         # ≥ this many distinctive chunk tokens in the answer


def _content_tokens(text: str) -> set[str]:
    """Distinctive content tokens (NFC-lower, len≥4, deduped) for overlap scoring.
    Generic — no language-specific stopword list; the length filter drops most
    function words while keeping content syllables/words."""
    norm = unicodedata.normalize("NFC", text or "").lower()
    return {t for t in re.split(r"[^0-9a-zà-ỹ_]+", norm) if len(t) >= 4}


def backfill_citations(
    answer: str, chunks: list[dict], existing: list[Citation]
) -> list[Citation]:
    """Cite the retrieved chunks an answer actually USED but didn't `[chunk_id]`-mark.

    A grounded answer that paraphrases (the model omitted the marker) still needs
    its source cited for grounding/scoring. For each not-yet-cited evidence chunk
    (top score first), attach a Citation when enough of the chunk's distinctive
    content tokens appear in the answer — so a refusal/one-liner (too short) or a
    chitchat answer (no KB-content overlap) gets ZERO backfill. Capped. Domain-free.
    """
    if not answer or len(answer) < _BACKFILL_MIN_ANSWER_CHARS or not chunks:
        return []
    cited = {c.chunk_id for c in existing}
    ans_tokens = _content_tokens(answer)
    if not ans_tokens:
        return []
    ranked = sorted(chunks, key=lambda c: float(c.get("score") or 0), reverse=True)
    out: list[Citation] = []
    for ch in ranked:
        cid = str(ch.get("chunk_id") or "")
        if not cid or cid in cited:
            continue
        ctoks = _content_tokens(ch.get("text") or "")
        if not ctoks:
            continue
        shared = len(ctoks & ans_tokens)
        # absolute overlap, or (for short chunks) a strong relative overlap
        if shared >= _BACKFILL_MIN_OVERLAP or (shared and shared >= 0.5 * len(ctoks)):
            cited.add(cid)
            out.append(Citation(
                chunk_id=cid,
                source_ref=str(ch.get("source_ref") or ""),
                supporting_span=(0, len(answer)),
                # Same propagation as citations_from_text — the backfill path
                # produces user-facing Citations that the delivery client will
                # render; they need the same structural metadata.
                page_number=ch.get("page_number"),
                metadata=dict(ch.get("metadata") or {}),
            ))
            if len(out) >= _BACKFILL_MAX_ADD:
                break
    return out


def extract_tool_citations(tool_output: str) -> list[Citation]:
    """Citations a tool emitted in its output — a KB tool may return a JSON object
    carrying a ``citations`` array. Tolerant: non-JSON / no-citations ⇒ ``[]``."""
    if not tool_output or "citations" not in tool_output:
        return []
    try:
        data = json.loads(tool_output)
    except (ValueError, TypeError):
        return []
    cits = data.get("citations") if isinstance(data, dict) else None
    if not isinstance(cits, list):
        return []
    out: list[Citation] = []
    for c in cits:
        try:
            out.append(Citation(**c) if isinstance(c, dict) else c)
        except (TypeError, ValueError):
            continue
    return out
