"""The plugin seam — ``Plugin`` + ``AgentSetup``.

A **plugin** is a self-contained unit that extends the agent at assembly time. It
is a *first-class plug-and-play component* that may contribute the **full capacity
surface** — lobes, stages, paths/flows, skills, and tools — plus event hooks,
guardrails, and seam bindings (like a filesystem). It is the single, composable
extension mechanism (``plugins=[…]``).

    @runtime_checkable
    class Plugin(Protocol):
        name: str
        def install(self, setup: AgentSetup) -> None: ...

``AgentSetup`` is the mutable builder a plugin receives — it collects everything
the plugin contributes, which the agent folds into its engine. A plugin may also
*remove* a builtin capability it owns/overrides (``remove_lobe``/``remove_path``/
``remove_flow``/``remove_skill``); the agent honors removals after every plugin
has installed, **never dropping a pinned lobe** (the citation/grounding guard).

The disable model is plug/unplug: a capability is resolvable only while its plugin
is in ``plugins=[…]``. Drop the plugin (or give it ``enabled = False``) and its
lobes/paths/flows/skills/tools are simply not registered.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

__all__ = ["Plugin", "AgentSetup", "Workspace"]


@runtime_checkable
class Plugin(Protocol):
    name: str

    def install(self, setup: AgentSetup) -> None: ...


@runtime_checkable
class Workspace(Protocol):
    """The seam a workspace driver binds (a virtual FS for artifacts/documents)."""

    async def read(self, path: str) -> bytes: ...
    async def write(self, path: str, data: bytes) -> None: ...
    async def list(self, prefix: str = "") -> list[str]: ...
    async def edit(self, path: str, patch: str) -> None: ...


class AgentSetup:
    """Mutable assembly-time builder handed to each plugin's ``install``."""

    def __init__(self) -> None:
        self.tools: list[Any] = []
        self.lobes: list[Any] = []
        self.stages: list[Any] = []
        self.flows: list[Any] = []
        self.paths: list[Any] = []
        self.skills: list[Any] = []
        # MCP servers (specs or runtimes) the agent connects + discovers in the
        # resolve phase, registering their tools downstream.
        self.mcp_servers: list[Any] = []
        self.event_hooks: list[Callable[[Any], Any]] = []
        self.pre_checks: list[Callable[[str], Any]] = []
        self.post_checks: list[Callable[[Any], Any]] = []
        self.prefetch_hooks: list[Callable[[str, Any], Any]] = []
        self.tool_filters: list[Callable[[str, str, dict], Any]] = []
        self.workspace: Workspace | None = None
        # Builtin capabilities a plugin owns/overrides and wants subtracted from
        # the resolved network (by id/name). Pinned lobes are never removed.
        self.removed_lobes: set[str] = set()
        self.removed_paths: set[str] = set()
        self.removed_flows: set[str] = set()
        self.removed_skills: set[str] = set()

    def add_tool(self, t: Any) -> None:
        self.tools.append(t)

    def add_lobe(self, lobe: Any) -> None:
        """Contribute a runtime ``Lobe`` (carries a ``.spec``)."""
        self.lobes.append(lobe)

    def add_stage(self, stage: Any) -> None:
        self.stages.append(stage)

    def add_flow(self, flow: Any) -> None:
        """Contribute a ``Flow`` (intent pipeline). Its ``PathSpec`` is derived
        from its stages unless an explicit path is also added via ``add_path``."""
        self.flows.append(flow)

    def add_path(self, path: Any) -> None:
        """Contribute an explicit ``PathSpec`` recognizer (advanced; most plugins
        only need ``add_flow``)."""
        self.paths.append(path)

    def add_skill(self, skill: Any) -> None:
        """Contribute a ``Skill`` (procedural knowledge, progressively disclosed)."""
        self.skills.append(skill)

    def add_mcp_server(self, server: Any, *, transport: Any = None) -> None:
        """Contribute an MCP server — a spec dict, an ``MCPServerSpec``, or an
        ``MCPToolRuntime``. The agent connects + discovers it in the resolve phase
        and registers its tools. ``transport`` injects an embedded/in-process or test
        transport for a spec."""
        from agent_sdk.mcp import MCPToolRuntime

        rt = server if isinstance(server, MCPToolRuntime) else MCPToolRuntime(server, transport=transport)
        self.mcp_servers.append(rt)

    def remove_lobe(self, lobe_id: str) -> None:
        """Subtract a builtin lobe this plugin owns/overrides (pinned lobes survive)."""
        self.removed_lobes.add(lobe_id)

    def remove_path(self, name: str) -> None:
        self.removed_paths.add(name)

    def remove_flow(self, name: str) -> None:
        self.removed_flows.add(name)

    def remove_skill(self, slug: str) -> None:
        self.removed_skills.add(slug)

    def on_event(self, hook: Callable[[Any], Any]) -> None:
        self.event_hooks.append(hook)

    def add_pre_check(self, check: Callable[[str], Any]) -> None:
        """A guardrail run on the user input before the turn (raise to block)."""
        self.pre_checks.append(check)

    def add_post_check(self, check: Callable[[Any], Any]) -> None:
        """A guardrail run on the ``AgentResult`` after the turn (raise to block)."""
        self.post_checks.append(check)

    def add_prefetch_hook(self, hook: Callable[[str, Any], Any]) -> None:
        """A per-turn async/sync ``hook(query, state) -> dict`` whose result is
        merged into the ``TurnContext`` (e.g. ``{"memory_items": [...]}``) before
        the lobes assemble context — the seam for always-on recall/index data."""
        self.prefetch_hooks.append(hook)

    def add_tool_filter(self, filt: Callable[[str, str, dict], Any]) -> None:
        """A guard ``filt(stage_id, tool_name, input) -> str | None`` run before a
        tool executes; return a string to short-circuit the call with that result
        (e.g. a redundant-write guard), or ``None`` to allow it."""
        self.tool_filters.append(filt)

    def bind_workspace(self, workspace: Workspace) -> None:
        self.workspace = workspace
