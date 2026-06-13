"""Filesystem tools — Read / Write / Edit / LS.

Claude Code's canonical names and param shapes (``file_path`` / ``old_string`` /
…) so the model leans on its training priors. Read returns ``cat -n`` style line
numbers and pages large files via ``offset``/``limit``; Edit is an exact-string
replace (Read first); both surface a "did you mean?" hint on a missing path.
"""

from __future__ import annotations

import os

from agent_sdk import Tool, tool

from coding_agent.tools.workspace import SKIP_DIRS, Workspace

_READ_MAX_LINES = 2000


def fs_tools(ws: Workspace) -> list[Tool]:
    """The read/write/edit/list tools bound to one :class:`Workspace`."""

    @tool(name="Read")
    async def read(file_path: str, offset: int = 1, limit: int = _READ_MAX_LINES) -> str:
        """Reads a file from the local filesystem. Returns ``cat -n`` style line
        numbers. For large files pass ``offset`` (1-based start line) and ``limit``
        (max lines, default 2000) to page through it."""
        full = ws.safe(file_path)
        if not os.path.isfile(full):
            return f"Error: not a file: {file_path}{ws.nearby(file_path)}"
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
        full = ws.safe(file_path)
        os.makedirs(os.path.dirname(full) or ws.root, exist_ok=True)
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
        full = ws.safe(file_path)
        if not os.path.isfile(full):
            return f"Error: not a file: {file_path}{ws.nearby(file_path)}"
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
        target = ws.safe(path)
        if not os.path.isdir(target):
            return f"Error: not a directory: {path}{ws.nearby(path)}"
        out = []
        for name in sorted(os.listdir(target)):
            if name in SKIP_DIRS:
                continue
            out.append(f"{name}/" if os.path.isdir(os.path.join(target, name)) else name)
        return "\n".join(out) or "(empty)"

    return [read, write, edit, ls]
