"""A deterministic, free repository map — the structure the model would otherwise
spend dozens of LS/Glob/Read hops (and path-guessing errors) to rediscover.

``build_repo_map`` walks the tree once and emits a compact directory listing with
each Python file's top-level symbols (classes + functions, via ``ast``). Injected
into the agent's instructions, it grounds the whole run in the *real* layout so the
model orients instead of inferring a conventional structure that may not exist.

Pure stdlib, no LLM, no network — it fits the SDK's "deterministic core, I/O at the
seams" model: same repo → same map.
"""

from __future__ import annotations

import ast
import os

_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache",
    ".ruff_cache", ".pytest_cache", "dist", "build", ".next", "target",
}
_MAX_SYMBOLS_PER_FILE = 8


def _top_level_symbols(path: str) -> list[str]:
    """Top-level class/def names in a Python file (best-effort, parse-tolerant)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return []
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
    return names[:_MAX_SYMBOLS_PER_FILE]


def build_repo_map(root: str, *, max_files: int = 600, max_chars: int = 6000) -> str:
    """A compact, deterministic structural map of ``root`` (tree + top-level symbols)."""
    root = os.path.abspath(root)
    rows: list[tuple[str, str]] = []  # (relative_path, symbol summary)
    nfiles = 0
    truncated = False
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for fname in sorted(files):
            if nfiles >= max_files:
                truncated = True
                break
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root)
            if fname.endswith(".py"):
                syms = _top_level_symbols(full)
                rows.append((rel, ", ".join(syms)))
            else:
                rows.append((rel, ""))
            nfiles += 1
        if nfiles >= max_files:
            truncated = True
            break

    lines = [
        "Repository map (deterministic — the REAL file tree + top-level symbols). "
        "Use these exact paths; do not guess at conventional layouts that may not exist.",
        "",
    ]
    last_dir = None
    for rel, syms in rows:
        d = os.path.dirname(rel)
        if d != last_dir:
            lines.append(f"{d}/" if d else ".")
            last_dir = d
        base = os.path.basename(rel)
        lines.append(f"  {base}" + (f" — {syms}" if syms else ""))
    if truncated:
        lines.append(f"… (map truncated at {max_files} files)")

    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars].rsplit("\n", 1)[0] + "\n… (map truncated to fit budget)"
    return out
