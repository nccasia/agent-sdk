"""``PluginWorkspace`` — a persistent, sandboxed file tree + ``fs.*`` tools.

Gives the agent a working document area for artifacts. The ``driver`` selects the backend —
``virtual`` (ephemeral) · ``local`` (disk) · ``s3`` — each in ``drivers.py``; the ``fs.*`` tools
are in ``tools.py``. Installing it binds the workspace and wires
``fs.read``/``fs.write``/``fs.list``/``fs.edit``.
"""

from __future__ import annotations

from typing import Any

from agent_sdk.plugins.base import AgentSetup, Workspace
from agent_sdk.plugins.workspace.drivers import (
    DRIVERS,
    LocalWorkspace,
    S3Workspace,
    VirtualWorkspace,
)
from agent_sdk.plugins.workspace.tools import FsToolRuntime

__all__ = [
    "PluginWorkspace",
    "VirtualWorkspace",
    "LocalWorkspace",
    "S3Workspace",
    "FsToolRuntime",
]


class PluginWorkspace:
    name = "workspace"

    def __init__(
        self,
        driver: str = "virtual",
        *,
        root: str | None = None,
        bucket: str | None = None,
        **kw: Any,
    ):
        if driver not in DRIVERS:
            raise ValueError(f"unknown workspace driver {driver!r}")
        if driver == "virtual":
            self.workspace: Workspace = VirtualWorkspace()
        elif driver == "local":
            self.workspace = LocalWorkspace(root or ".agent-fs")
        else:
            self.workspace = S3Workspace(bucket or "", **kw)

    def install(self, setup: AgentSetup) -> None:
        setup.bind_workspace(self.workspace)
        setup.add_tool(FsToolRuntime(self.workspace))
