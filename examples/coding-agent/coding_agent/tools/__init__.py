"""A Claude-Code-grade toolset over a real workspace directory.

The agent navigates and edits a *large* real repository with the same primitives
a top coding agent uses, grouped by concern:

- :mod:`coding_agent.tools.fs` — Read / Write / Edit / LS
- :mod:`coding_agent.tools.search` — Glob / Grep
- :mod:`coding_agent.tools.shell` — Bash

Everything operates on a real path on disk, sandboxed under the workspace root
(:class:`coding_agent.tools.workspace.Workspace`). Designed for *long* agentic
runs (hundreds of tool calls): outputs are bounded per call (so one tool result
never floods the window) while the engine's Funnel ReAct demotes spent
observations to hints across hops.
"""

from __future__ import annotations

import sys

from agent_sdk import Tool

from coding_agent.tools.fs import fs_tools
from coding_agent.tools.search import search_tools
from coding_agent.tools.shell import shell_tools
from coding_agent.tools.workspace import Workspace

# Run the workspace's tests with the SAME interpreter the agent runs under.
PYTEST_CMD = f"{sys.executable} -m pytest -q"


def coding_tools(root: str) -> list[Tool]:
    """Build the workspace-bound Claude-Code-grade tools rooted at ``root``."""
    ws = Workspace(root)
    return [*fs_tools(ws), *search_tools(ws), *shell_tools(ws)]


__all__ = ["coding_tools", "PYTEST_CMD", "Workspace"]
