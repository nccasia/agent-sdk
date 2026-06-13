"""Shell tool — Bash, sandboxed to the workspace (cwd=root, PYTHONPATH=root).

Bounded output (so one command never floods the window) and a hard timeout, for
build / test / git during long agentic runs.
"""

from __future__ import annotations

import asyncio
import os

from agent_sdk import Tool, tool

from coding_agent.tools.workspace import Workspace

_BASH_MAX_OUTPUT = 30_000


def shell_tools(ws: Workspace) -> list[Tool]:
    """The bash tool bound to one :class:`Workspace`."""

    @tool(name="Bash")
    async def bash(command: str) -> str:
        """Executes a shell command in the workspace (build, run tests, git, …).
        180s timeout. Use Read/Glob/Grep — not cat/find/grep — to inspect files."""
        proc = await asyncio.create_subprocess_shell(
            command, cwd=ws.root,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONPATH": ws.root},
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

    return [bash]
