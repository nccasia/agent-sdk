"""Search tools — Glob (file-name matching) and Grep (content regex).

Both walk the workspace once, skipping VCS/cache/build dirs, and cap their output
so one result never floods the window on a large repo.
"""

from __future__ import annotations

import fnmatch
import os
import re

from agent_sdk import Tool, tool

from coding_agent.tools.workspace import SKIP_DIRS, Workspace

_GREP_MAX_HITS = 200
_GLOB_MAX = 300


def search_tools(ws: Workspace) -> list[Tool]:
    """The glob/grep tools bound to one :class:`Workspace`."""

    @tool(name="Glob")
    async def glob(pattern: str, path: str = ".") -> str:
        """Fast file matching by glob pattern (e.g. ``**/*.py``, ``apps/**/test_*.py``).
        Returns matching paths sorted by modification time (newest first)."""
        base = ws.safe(path)
        hits: list[tuple[float, str]] = []
        recursive = "**" in pattern
        # A leading `**/` means "any depth, including zero" — but fnmatch has no
        # true `**` and requires the `/` to be present, so `**/foo.py` would miss a
        # root-level `foo.py`. Also try the pattern with the leading `**/` stripped
        # so root files match the way a real glob expects.
        root_pattern = pattern[3:] if pattern.startswith("**/") else None
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in files:
                full = os.path.join(dirpath, fname)
                rel = ws.rel(full)
                matchee = rel if recursive else fname
                if (
                    fnmatch.fnmatch(matchee, pattern)
                    or fnmatch.fnmatch(rel, pattern)
                    or (root_pattern is not None and fnmatch.fnmatch(rel, root_pattern))
                ):
                    try:
                        hits.append((os.path.getmtime(full), rel))
                    except OSError:
                        continue
            if not recursive:
                break
        hits.sort(reverse=True)
        out = [r for _, r in hits[:_GLOB_MAX]]
        tail = f"\n… ({len(hits) - _GLOB_MAX} more)" if len(hits) > _GLOB_MAX else ""
        return "\n".join(out) + tail if out else "(no files match)"

    @tool(name="Grep")
    async def grep(pattern: str, path: str = ".", glob: str = "") -> str:
        """Searches file contents with a regex. Optional ``glob`` filters files
        (e.g. ``*.py``). Returns ``file:line: match``."""
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return f"Bad regex: {exc}"
        base = ws.safe(path)
        hits: list[str] = []
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in files:
                if glob and not fnmatch.fnmatch(fname, glob):
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    with open(fpath, encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f, 1):
                            if rx.search(line):
                                hits.append(f"{ws.rel(fpath)}:{i}: {line.rstrip()}")
                                if len(hits) >= _GREP_MAX_HITS:
                                    return "\n".join(hits) + f"\n… (≥{_GREP_MAX_HITS} matches; narrow the pattern/glob)"
                except OSError:
                    continue
        return "\n".join(hits) or "(no matches)"

    return [glob, grep]
