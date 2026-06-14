"""Leaf invariant — ``agent_sdk`` must import nothing project-side.

The whole point of the standalone SDK is that it is a *leaf*: it may import the
standard library and third-party deps (``anthropic``, ``numpy``, ``pydantic``,
``cachetools``, ``pyyaml``, optionally ``openai`` / ``redis``) plus other ``agent_sdk``
modules — but never the Mezon project packages (``rag_core`` / ``arag_core`` /
``ingest_core`` / ``agent_core``). This AST walk fails on the first violation.
"""

from __future__ import annotations

import ast
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parent.parent / "agent_sdk"

FORBIDDEN_ROOTS = {"rag_core", "arag_core", "ingest_core", "agent_core"}


def _imported_modules(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def _sdk_files() -> list[Path]:
    return sorted(p for p in SDK_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def test_sdk_imports_no_project_packages() -> None:
    assert SDK_ROOT.is_dir(), f"SDK root not found: {SDK_ROOT}"
    violations: list[str] = []
    for path in _sdk_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(SDK_ROOT.parent)
        for mod in _imported_modules(tree):
            root = mod.split(".", 1)[0]
            if root in FORBIDDEN_ROOTS:
                violations.append(f"{rel}: imports forbidden package {mod!r}")
    assert not violations, "SDK leaf invariant broken:\n  " + "\n  ".join(violations)
