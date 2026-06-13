"""Workspace — the sandbox root + path helpers shared by every coding tool.

Every tool operates on a real path on disk, sandboxed under one workspace root.
This holds the root, the path-safety guard (no escaping the workspace), and the
"did you mean?" self-correction hint — so the fs / search / shell tool modules
stay focused on their own behavior instead of repeating the plumbing.
"""

from __future__ import annotations

import difflib
import os

# Directories the tools never walk or list (VCS, caches, build output).
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache",
    ".ruff_cache", ".pytest_cache", "dist", "build", ".next", "target",
}


class Workspace:
    """A real directory the tools are sandboxed to (all paths resolve under it)."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    def safe(self, path: str) -> str:
        """Resolve ``path`` under the root, rejecting any escape (``..`` / absolute)."""
        full = os.path.abspath(os.path.join(self.root, path.lstrip("/")))
        if not (full == self.root or full.startswith(self.root + os.sep)):
            raise ValueError(f"path escapes workspace: {path!r}")
        return full

    def rel(self, full: str) -> str:
        return os.path.relpath(full, self.root)

    def nearby(self, path: str) -> str:
        """A self-correction hint for a path that doesn't exist: name the closest
        real siblings in its deepest existing ancestor dir. Turns a dead-end
        ``not a file`` error into a one-hop recovery instead of another guess."""
        parts = [p for p in path.strip("/").split("/") if p]
        cur, missing = self.root, path
        for part in parts:
            nxt = os.path.join(cur, part)
            if os.path.isdir(nxt):
                cur = nxt
                continue
            missing = part
            break
        try:
            entries = [e for e in sorted(os.listdir(cur)) if e not in SKIP_DIRS]
        except OSError:
            return ""
        where = self.rel(cur) if cur != self.root else "."
        close = difflib.get_close_matches(missing, entries, n=3, cutoff=0.4)
        if close:
            return f" — in {where!r}, did you mean: {', '.join(close)}?"
        shown = ", ".join(entries[:8]) + (" …" if len(entries) > 8 else "")
        return f" — {where!r} contains: {shown}" if shown else ""
