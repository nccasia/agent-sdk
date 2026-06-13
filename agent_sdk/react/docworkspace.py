"""DocWorkspace — the heavy-document capability for PreAct.

The `memory` tool stores one fact per (scope, key) and recalls it WHOLE. That is
fatal for the heavy form — a user pastes a large Markdown document and asks for a
transform (md→html, summarize-each, extract-all). Keeping the doc in the prompt
floods the window; recalling it whole re-floods. What's needed is the file-tool
discipline a long-running coding agent uses: **offload the body, then read it the
way `grep`/`glob`/`Read(offset)` do — by structure and by slice, never whole.**

A DocWorkspace offloads a document out of the prompt and exposes:

* ``offload(doc_id, text)`` → an OUTLINE (headings only — the `glob`/`ls` view).
  The body stays in the workspace, never in the returned value.
* ``outline(doc_id)`` → the section index (id, heading, level, char span).
* ``grep(doc_id, pattern)`` → matching lines + their section (the `grep` view) —
  returns matches, not the document.
* ``read_section(doc_id, section_id)`` → ONE section's text (the `Read` slice) —
  the only way the body enters context, one bounded part at a time.
* ``write_part(doc_id, section_id, content)`` → store a transformed part back.
* ``assemble(doc_id)`` → concatenate the written parts in order (the long-form
  output), built without ever holding the whole source + whole output together.

Pure, deterministic, in-process. The MCP tool wrapper (exposing these to the
model in the loop) is the live-integration layer on top of this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class _Section:
    id: str
    heading: str
    level: int
    start: int  # char offset of the body (after the heading line)
    end: int


@dataclass
class _Doc:
    text: str
    sections: list[_Section]
    parts: dict[str, str] = field(default_factory=dict)  # section_id -> transformed output


def _slug(heading: str, idx: int) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
    return f"{idx:02d}-{s}"[:48] if s else f"sec-{idx:02d}"


def parse_sections(text: str) -> list[_Section]:
    """Split a Markdown document into sections on ATX headings. Each section spans
    from its heading to the next heading of the same-or-higher level (its body)."""
    lines = text.splitlines(keepends=True)
    heads: list[tuple[int, int, str]] = []  # (char_offset_of_line, level, heading)
    off = 0
    for ln in lines:
        m = _HEADING_RE.match(ln.strip("\n"))
        if m:
            heads.append((off, len(m.group(1)), m.group(2).strip()))
        off += len(ln)
    out: list[_Section] = []
    for i, (start_line, level, heading) in enumerate(heads):
        body_start = start_line + len(heading) + level + 2  # past "## heading\n"
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        out.append(
            _Section(
                id=_slug(heading, i),
                heading=heading,
                level=level,
                start=min(body_start, end),
                end=end,
            )
        )
    return out


class DocWorkspace:
    """In-process store of offloaded documents. Bodies live here; only outlines,
    grep matches, and single sections ever leave."""

    def __init__(self) -> None:
        self.docs: dict[str, _Doc] = {}

    # ── offload + structure (glob / ls) ──────────────────────────────────────
    def offload(self, doc_id: str, text: str) -> dict[str, Any]:
        sections = parse_sections(text)
        self.docs[doc_id] = _Doc(text=text, sections=sections)
        return {
            "doc_id": doc_id,
            "total_chars": len(text),
            "sections": len(sections),
            "outline": self.outline(doc_id),
        }

    def outline(self, doc_id: str) -> list[dict[str, Any]]:
        doc = self.docs[doc_id]
        return [
            {"id": s.id, "heading": s.heading, "level": s.level, "chars": s.end - s.start}
            for s in doc.sections
        ]

    # ── grep (matches, not the document) ─────────────────────────────────────
    def grep(self, doc_id: str, pattern: str, *, max_matches: int = 50) -> list[dict[str, Any]]:
        doc = self.docs[doc_id]
        rx = re.compile(pattern, re.IGNORECASE)
        out: list[dict[str, Any]] = []
        for s in doc.sections:
            body = doc.text[s.start : s.end]
            for line in body.splitlines():
                if rx.search(line):
                    out.append(
                        {"section_id": s.id, "heading": s.heading, "line": line.strip()[:200]}
                    )
                    if len(out) >= max_matches:
                        return out
        return out

    # ── read one slice (the only path the body enters context) ───────────────
    def read_section(self, doc_id: str, section_id: str) -> str:
        doc = self.docs[doc_id]
        for s in doc.sections:
            if s.id == section_id:
                return doc.text[s.start : s.end].strip()
        raise KeyError(section_id)

    # ── partial write + long-form assembly ───────────────────────────────────
    def write_part(self, doc_id: str, section_id: str, content: str) -> dict[str, Any]:
        doc = self.docs[doc_id]
        if not any(s.id == section_id for s in doc.sections):
            raise KeyError(section_id)
        doc.parts[section_id] = content
        return {
            "doc_id": doc_id,
            "section_id": section_id,
            "written_parts": len(doc.parts),
            "total_parts": len(doc.sections),
        }

    def assemble(self, doc_id: str) -> str:
        """Concatenate written parts in document order — the long-form result.
        Sections without a written part are skipped (the caller can detect gaps
        via ``written_parts`` vs ``total_parts``)."""
        doc = self.docs[doc_id]
        return "\n\n".join(doc.parts[s.id] for s in doc.sections if s.id in doc.parts)
