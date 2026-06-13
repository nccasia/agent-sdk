"""A Claude-Code-grade toolset over a real workspace directory.

The agent navigates and edits a *large* real repository with the same primitives
a top coding agent uses: read (with line numbers + offset/limit for big files),
write, exact-string edit, directory listing, glob file-finding, content grep, and
shell. Everything operates on a real path on disk and is sandboxed under the
workspace root.

Designed for *long* agentic runs (hundreds of tool calls): outputs are bounded
per call (so one tool result never floods the window) while the engine's Funnel
ReAct demotes spent observations to hints across hops.
"""

from __future__ import annotations

import asyncio
import difflib
import fnmatch
import os
import re

from agent_sdk import Tool, tool

_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache",
    ".ruff_cache", ".pytest_cache", "dist", "build", ".next", "target",
}
_READ_MAX_LINES = 2000
_GREP_MAX_HITS = 200
_GLOB_MAX = 300
_BASH_MAX_OUTPUT = 30_000


def coding_tools(root: str) -> list[Tool]:
    """Build the workspace-bound Claude-Code-grade tools rooted at ``root``."""
    root = os.path.abspath(root)

    def _safe(path: str) -> str:
        full = os.path.abspath(os.path.join(root, path.lstrip("/")))
        if not (full == root or full.startswith(root + os.sep)):
            raise ValueError(f"path escapes workspace: {path!r}")
        return full

    def _rel(full: str) -> str:
        return os.path.relpath(full, root)

    def _nearby(path: str) -> str:
        """A self-correction hint for a path that doesn't exist: name the closest
        real siblings in its deepest existing ancestor dir. Turns a dead-end
        ``not a file`` error into a one-hop recovery instead of another guess."""
        parts = [p for p in path.strip("/").split("/") if p]
        cur, missing = root, path
        for part in parts:
            nxt = os.path.join(cur, part)
            if os.path.isdir(nxt):
                cur = nxt
                continue
            missing = part
            break
        try:
            entries = [e for e in sorted(os.listdir(cur)) if e not in _SKIP_DIRS]
        except OSError:
            return ""
        where = _rel(cur) if cur != root else "."
        close = difflib.get_close_matches(missing, entries, n=3, cutoff=0.4)
        if close:
            return f" — in {where!r}, did you mean: {', '.join(close)}?"
        shown = ", ".join(entries[:8]) + (" …" if len(entries) > 8 else "")
        return f" — {where!r} contains: {shown}" if shown else ""

    @tool(name="Read")
    async def read(file_path: str, offset: int = 1, limit: int = _READ_MAX_LINES) -> str:
        """Reads a file from the local filesystem. Returns ``cat -n`` style line
        numbers. For large files pass ``offset`` (1-based start line) and ``limit``
        (max lines, default 2000) to page through it."""
        full = _safe(file_path)
        if not os.path.isfile(full):
            return f"Error: not a file: {file_path}{_nearby(file_path)}"
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as exc:
            return f"Error reading {file_path}: {exc}"
        start = max(1, int(offset))
        end = min(len(lines), start - 1 + max(1, int(limit)))
        if start > len(lines):
            return f"(file has {len(lines)} lines; offset {start} is past the end)"
        body = "".join(f"{i:>6}\t{lines[i - 1].rstrip(chr(10))}\n" for i in range(start, end + 1))
        more = f"\n… ({len(lines) - end} more lines; read with offset={end + 1})" if end < len(lines) else ""
        return body + more

    @tool(name="Write")
    async def write(file_path: str, content: str) -> str:
        """Writes a file, overwriting if it exists. Prefer Edit for changes to an
        existing file."""
        full = _safe(file_path)
        os.makedirs(os.path.dirname(full) or root, exist_ok=True)
        try:
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as exc:
            return f"Error writing {file_path}: {exc}"
        return f"Wrote {file_path} ({content.count(chr(10)) + 1} lines)."

    @tool(name="Edit")
    async def edit(file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        """Performs an exact string replacement in a file. ``old_string`` must be
        unique unless ``replace_all`` is set — Read the file first so the match is
        exact (including indentation)."""
        full = _safe(file_path)
        if not os.path.isfile(full):
            return f"Error: not a file: {file_path}{_nearby(file_path)}"
        text = open(full, encoding="utf-8", errors="replace").read()
        count = text.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {file_path}."
        if count > 1 and not replace_all:
            return f"Error: old_string is not unique in {file_path} ({count} matches) — add context or set replace_all."
        new = text.replace(old_string, new_string)
        with open(full, "w", encoding="utf-8") as f:
            f.write(new)
        return f"Edited {file_path} ({count} replacement{'s' if count != 1 else ''})."

    @tool(name="LS")
    async def ls(path: str = ".") -> str:
        """Lists files and directories (dirs marked with /) under a path."""
        target = _safe(path)
        if not os.path.isdir(target):
            return f"Error: not a directory: {path}{_nearby(path)}"
        out = []
        for name in sorted(os.listdir(target)):
            if name in _SKIP_DIRS:
                continue
            out.append(f"{name}/" if os.path.isdir(os.path.join(target, name)) else name)
        return "\n".join(out) or "(empty)"

    @tool(name="Glob")
    async def glob(pattern: str, path: str = ".") -> str:
        """Fast file matching by glob pattern (e.g. ``**/*.py``, ``apps/**/test_*.py``).
        Returns matching paths sorted by modification time (newest first)."""
        base = _safe(path)
        hits: list[tuple[float, str]] = []
        recursive = "**" in pattern
        # A leading `**/` means "any depth, including zero" — but fnmatch has no
        # true `**` and requires the `/` to be present, so `**/foo.py` would miss a
        # root-level `foo.py`. Also try the pattern with the leading `**/` stripped
        # so root files match the way a real glob expects.
        root_pattern = pattern[3:] if pattern.startswith("**/") else None
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fname in files:
                full = os.path.join(dirpath, fname)
                rel = _rel(full)
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
        base = _safe(path)
        hits: list[str] = []
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fname in files:
                if glob and not fnmatch.fnmatch(fname, glob):
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    with open(fpath, encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f, 1):
                            if rx.search(line):
                                hits.append(f"{_rel(fpath)}:{i}: {line.rstrip()}")
                                if len(hits) >= _GREP_MAX_HITS:
                                    return "\n".join(hits) + f"\n… (≥{_GREP_MAX_HITS} matches; narrow the pattern/glob)"
                except OSError:
                    continue
        return "\n".join(hits) or "(no matches)"

    @tool(name="Bash")
    async def bash(command: str) -> str:
        """Executes a shell command in the workspace (build, run tests, git, …).
        180s timeout. Use Read/Glob/Grep — not cat/find/grep — to inspect files."""
        proc = await asyncio.create_subprocess_shell(
            command, cwd=root,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONPATH": root},
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
        except TimeoutError:
            proc.kill()
            return f"Command timed out after 180s: {command}"
        text = out.decode("utf-8", "ignore")
        if len(text) > _BASH_MAX_OUTPUT:
            head, tail = text[: _BASH_MAX_OUTPUT // 2], text[-_BASH_MAX_OUTPUT // 2 :]
            text = head + f"\n… ({len(text) - _BASH_MAX_OUTPUT} chars elided) …\n" + tail
        return f"$ {command}\n(exit {proc.returncode})\n{text}"

    return [read, write, edit, ls, glob, grep, bash]


import sys  # noqa: E402

PYTEST_CMD = f"{sys.executable} -m pytest -q"
