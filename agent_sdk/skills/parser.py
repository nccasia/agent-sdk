"""Skill parser — deterministic markdown navigation (RFC 0013 layered skills).

The reading layer: split a bundle into sections, render a table of contents for a
large file, estimate tokens, and keyword-search across every section of every file.
So the model reads a large bundle progressively — index → ToC → one section —
instead of dumping files. Pure functions, no I/O.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_sdk.skills.packs import SkillPack

# A file at or below this estimated size is returned whole; above it, a bare
# file read returns the ToC and the model requests a section.
FULL_FILE_TOKENS = 1500


@dataclass(frozen=True)
class Section:
    id: str
    heading: str
    content: str
    line_count: int


def est_tokens(text: str) -> int:
    return max(0, len(text or "") // 4)


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


def _slugify_heading(heading: str) -> str:
    norm = unicodedata.normalize("NFKD", heading)
    ascii_text = norm.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug or "section"


def split_sections(markdown: str) -> list[Section]:
    """Split markdown by #/##/### headings. Content before the first heading
    becomes a synthetic "intro" section. Section ids are slugified headings,
    deduped with -2, -3 … suffixes."""
    text = markdown or ""
    matches = list(_HEADING_RE.finditer(text))
    sections: list[Section] = []
    seen: dict[str, int] = {}

    def add(heading: str, content: str) -> None:
        base = _slugify_heading(heading)
        seen[base] = seen.get(base, 0) + 1
        sid = base if seen[base] == 1 else f"{base}-{seen[base]}"
        sections.append(
            Section(
                id=sid,
                heading=heading,
                content=content.strip("\n"),
                line_count=content.count("\n") + 1,
            )
        )

    if not matches:
        if text.strip():
            add("intro", text)
        return sections

    if text[: matches[0].start()].strip():
        add("intro", text[: matches[0].start()])
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        add(m.group(2), text[m.start() : end])
    return sections


def file_toc(content: str) -> str:
    """A table of contents for a large file: section ids, headings, sizes."""
    lines = ["Table of contents (request one section at a time):"]
    for sec in split_sections(content):
        lines.append(f"- [{sec.id}] {sec.heading} (~{est_tokens(sec.content)} tokens)")
    return "\n".join(lines)


def file_purpose(content: str) -> str:
    """One-line purpose for the layer-2 file index: frontmatter description,
    else the first heading, else the first non-empty line."""
    text = (content or "").lstrip()
    if text.startswith("---"):
        for line in text.splitlines()[1:30]:
            if line.strip() == "---":
                break
            if line.lower().startswith("description:"):
                return line.split(":", 1)[1].strip()
    m = _HEADING_RE.search(text)
    if m:
        return m.group(2)
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:120]
    return "(empty)"


def _nfc_lower(s: str) -> str:
    return unicodedata.normalize("NFC", s or "").lower()


def search_bundle(packs: list[SkillPack], query: str, top_k: int = 5) -> list[dict]:
    """Keyword search over every section of every file in the given skills.
    Deterministic token-overlap scoring (NFC-normalized for Vietnamese) — the
    fast path through very large bundles. Returns
    {skill, file, section, heading, score, snippet} hits."""
    terms = [t for t in re.split(r"\W+", _nfc_lower(query)) if len(t) > 1]
    if not terms:
        return []
    hits: list[dict] = []
    for pack in packs:
        sources = {"SKILL.md": pack.instructions, **pack.files}
        for path, content in sources.items():
            for sec in split_sections(content):
                hay = _nfc_lower(sec.heading + "\n" + sec.content)
                score = sum(hay.count(t) for t in terms)
                if score <= 0:
                    continue
                # Snippet: first line containing a term, trimmed.
                snippet = ""
                for line in sec.content.splitlines():
                    if any(t in _nfc_lower(line) for t in terms):
                        snippet = line.strip()[:200]
                        break
                hits.append(
                    {
                        "skill": pack.name or pack.id,
                        "file": path,
                        "section": sec.id,
                        "heading": sec.heading,
                        "score": score,
                        "snippet": snippet,
                    }
                )
    hits.sort(key=lambda h: (-h["score"], h["file"], h["section"]))
    return hits[:top_k]
