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
    "strip_citation_markers",
    "citations_from_text",
    "backfill_citations",
    "extract_tool_citations",
]

# Inline grounding markers the model emits — ``[<chunk_id>]``, ``[id1, id2]``
# (uuid or short-hex), ``[golden:<case>]``. They drive citation EXTRACTION; once
# citations are on the result they are internal noise in the user-facing text, so
# they are stripped from the final answer (the citations ride in result.citations /
# message metadata and are rendered separately by the client).
_CITE_MARKER_RE = re.compile(
    r"\s*\[\s*(?:golden:[^\]]+"
    r"|[0-9a-fA-F][0-9a-fA-F-]{5,}(?:\s*,\s*[0-9a-fA-F][0-9a-fA-F-]{5,})*)\s*\]"
)


def strip_citation_markers(text: str) -> str:
    """Remove inline ``[chunk_id]`` / ``[golden:…]`` grounding markers from the
    user-facing answer (citations are preserved in the result, rendered separately).
    Leaves ordinary brackets ([1], [2025], markdown links) untouched."""
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
