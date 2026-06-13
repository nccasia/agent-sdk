"""``PluginMCP`` — mount an MCP server's tools.

``spec=`` mounts a full MCP server resolved via the connect→discover phase (status check +
``tools/list``), then registers its discovered tools; ``url=``/``runtime=``/``tools=`` mount a
server with static specs. The MCP runtime/spec types live in ``agent_sdk.mcp`` and are
re-exported here for convenience.
"""

from __future__ import annotations

from typing import Any

from agent_sdk.mcp import MCPError, MCPServerSpec, MCPToolRuntime
from agent_sdk.plugins.base import AgentSetup

__all__ = ["PluginMCP", "HTTPMCPToolRuntime", "MCPToolRuntime", "MCPServerSpec", "MCPError"]


class HTTPMCPToolRuntime:
    """A ``ToolRuntime`` proxying an external MCP server over HTTP (static specs).

    The class name is recognized by ``CompositeToolRuntime.external_names`` so adaptive
    selection never scores these tools out.
    """

    def __init__(
        self, url: str | None = None, *, specs: list[dict] | None = None, call: Any | None = None
    ):
        self.url = url
        self._specs = specs or []
        self._call = call

    def get_tool_specs(self) -> list[dict]:
        return list(self._specs)

    async def call_tool(self, name: str, inp: dict, retrieved_chunks=None, already_read=None) -> str:
        if self._call is not None:
            out = self._call(name, inp)
            if hasattr(out, "__await__"):
                out = await out
            return str(out)
        if not self.url:
            return f"Error: MCP tool {name!r} has no transport configured."
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.url}/call", json={"name": name, "input": inp})
            return resp.text


class PluginMCP:
    name = "mcp"

    def __init__(
        self,
        url: str | None = None,
        *,
        spec: Any | None = None,
        tools: list[Any] | None = None,
        runtime: Any | None = None,
        specs: list[dict] | None = None,
        call: Any | None = None,
        transport: Any | None = None,
    ):
        # spec= mounts a full MCP server resolved via the connect→discover phase (status check
        # + tools/list); the agent registers its discovered tools.
        self.mcp_server = None
        self.runtime = None
        if spec is not None:
            self.mcp_server = MCPToolRuntime(spec, transport=transport)
        elif runtime is not None:
            self.runtime = runtime
        elif tools is not None:
            from agent_sdk.tools import FunctionToolRuntime

            self.runtime = FunctionToolRuntime(tools)
        else:
            self.runtime = HTTPMCPToolRuntime(url, specs=specs, call=call)

    def install(self, setup: AgentSetup) -> None:
        if self.mcp_server is not None:
            setup.add_mcp_server(self.mcp_server)
        else:
            setup.add_tool(self.runtime)
