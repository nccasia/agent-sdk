"""The tool-runtime boundary — the generic contract between an agent harness
and its executable tools, plus the composite that fans calls across runtimes.

This is the unopinionated half of the tool layer: the ``ToolRuntime`` protocol
and ``CompositeToolRuntime`` carry no knowledge of any concrete tool. The
project-side concrete runtimes (KB/MCP/skill/memory/task — which reach into
``arag_core`` / ``rag_core``) implement this protocol in
``agent_core.tool_runtime``.
"""

from __future__ import annotations

from typing import Protocol


class ToolRuntime(Protocol):
    """Runtime boundary between the agent harness and executable tools."""

    def get_tool_specs(self) -> list[dict]:
        """Return Anthropic-compatible tool specs available for this turn."""
        ...

    async def call_tool(
        self,
        name: str,
        inp: dict,
        retrieved_chunks: list[dict],
        already_read: set[str],
    ) -> str:
        """Execute one tool call and return model-visible tool output."""
        ...


class CompositeToolRuntime:
    """Combine built-in tools with zero or more MCP runtimes."""

    def __init__(self, runtimes: list[ToolRuntime]):
        self._runtimes = runtimes
        self._tool_owner: dict[str, ToolRuntime] = {}

    def get_tool_specs(self) -> list[dict]:
        specs: list[dict] = []
        self._tool_owner = {}
        for runtime in self._runtimes:
            for spec in runtime.get_tool_specs():
                name = spec.get("name")
                if not name or name in self._tool_owner:
                    continue
                self._tool_owner[name] = runtime
                specs.append(spec)
        return specs

    def external_names(self) -> set[str]:
        """Tool names served by an EXTERNAL (HTTP/SSE) MCP installation — NOT
        the engine's well-known surface. Adaptive tool selection never scores
        these out (the engine has no curated relevance for third-party tools);
        they are always exposed."""
        if not getattr(self, "_tool_owner", None):
            self.get_tool_specs()
        return {
            name
            for name, rt in self._tool_owner.items()
            if type(rt).__name__ in ("HTTPMCPToolRuntime", "MCPToolRuntime")
        }

    async def call_tool(
        self,
        name: str,
        inp: dict,
        retrieved_chunks: list[dict],
        already_read: set[str],
    ) -> str:
        runtime = self._tool_owner.get(name)
        if runtime is None:
            # Tool specs are normally loaded before a call; refresh defensively
            # for direct tests or future runtimes that skip the LLM loop.
            self.get_tool_specs()
            runtime = self._tool_owner.get(name)
        if runtime is None:
            return f"Error: unknown tool '{name}'. Use only the provided tools."
        return await runtime.call_tool(name, inp, retrieved_chunks, already_read)
