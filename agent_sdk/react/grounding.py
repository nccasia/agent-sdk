"""``DocGroundingGuard`` — a tool-call filter that keeps a written document's
file references *grounded in reality*.

A capable model writing an architecture/summary doc will confidently cite paths
that don't exist — a conventional layout it inferred rather than the real one
(observed live: a doc naming ``guards/docguard.py`` when the file is at
``react/docguard.py``). That is the coding-agent analog of the RAG invariant
"refuse rather than emit ungrounded claims": a documented path should resolve to
a real file.

This guard watches write calls for documents (``doc_suffixes``) and, when the
content references paths that ``exists`` reports as absent, steers the model to
fix them (it has the deterministic repo map + the files it has read). It steers a
bounded number of times per document so it cannot deadlock the stage — after that
it lets the write through (a measurement gate still records the defect). It is
generic: pass an ``exists`` predicate and the read/write tool names.

``record_only=True`` logs ``events`` without intercepting.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

__all__ = ["DocGroundingGuard"]

# Path-ish tokens: a dotted code/file path, optionally with directories.
_PATH_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]{1,6}")
_CODE_SUFFIXES = (
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".md",
    ".toml",
    ".json",
    ".yaml",
    ".yml",
    ".cfg",
    ".ini",
    ".sh",
)


class DocGroundingGuard:
    """A tool-call filter that refuses a document citing nonexistent file paths."""

    def __init__(
        self,
        *,
        exists: Callable[[str], bool],
        read_tools: tuple[str, ...] = ("Read",),
        write_tools: tuple[str, ...] = ("Write",),
        path_keys: tuple[str, ...] = ("file_path", "path", "file", "filename"),
        content_key: str = "content",
        doc_suffixes: tuple[str, ...] = (".md",),
        max_refusals: int = 2,
        max_report: int = 10,
        record_only: bool = False,
    ) -> None:
        self.exists = exists
        self.read_tools = set(read_tools)
        self.write_tools = set(write_tools)
        self.path_keys = path_keys
        self.content_key = content_key
        self.doc_suffixes = doc_suffixes
        self.max_refusals = max_refusals
        self.max_report = max_report
        self.record_only = record_only
        self.read: set[str] = set()  # files actually read this run
        self._refusals: dict[str, int] = {}  # doc path -> times steered
        self.events: list[dict] = []

    def _path(self, inp: dict) -> str | None:
        for k in self.path_keys:
            v = inp.get(k)
            if isinstance(v, str) and v:
                return v
        return None

    def __call__(self, stage_id: str, name: str, inp: dict[str, Any]) -> str | None:
        # Track reads so "grounded" can later mean read-and-exists if desired.
        if name in self.read_tools:
            p = self._path(inp)
            if p:
                self.read.add(p.lstrip("/"))
            return None
        if name not in self.write_tools:
            return None
        path = self._path(inp)
        if path is None or not path.endswith(self.doc_suffixes):
            return None
        content = str(inp.get(self.content_key, "") or "")
        refs = [m for m in dict.fromkeys(_PATH_RE.findall(content)) if m.endswith(_CODE_SUFFIXES)]
        missing = [r for r in refs if not self.exists(r.lstrip("/"))]
        if not missing:
            return None
        self.events.append(
            {
                "stage": stage_id,
                "path": path,
                "action": "ungrounded_refs",
                "missing": missing[: self.max_report],
            }
        )
        self._refusals[path] = self._refusals.get(path, 0) + 1
        if self.record_only or self._refusals[path] > self.max_refusals:
            return None  # bounded — don't deadlock the stage; a gate still records it
        shown = ", ".join(missing[: self.max_report])
        more = (
            "" if len(missing) <= self.max_report else f" (+{len(missing) - self.max_report} more)"
        )
        return (
            f"Refused: {path!r} references paths that do not exist: {shown}{more}. "
            "Correct them to real paths from the repository map / the files you've read "
            "(or remove the claim), then write again."
        )
