"""MCP support — connect, check status, discover schema, build tool specs, register.

Drives the real ``MCPToolRuntime`` + ``PreactAgent`` resolve phase over an in-process fake
MCP transport (a JSON-RPC handler), so no network is needed. Mirrors what a live MCP server
would answer for ``initialize`` / ``tools/list`` / ``tools/call``.
"""

from __future__ import annotations

from agent_sdk import MCPToolRuntime, PreactAgent
from agent_sdk.clients.fake import FakeClient
from agent_sdk.plugins import PluginMCP

WEATHER_TOOL = {
    "name": "weather",
    "description": "Current weather for a city.",
    "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}},
}


def fake_mcp(tools, *, record=None):
    """A sync JSON-RPC handler emulating an MCP server over the transport seam."""

    def transport(req: dict) -> dict:
        if record is not None:
            record.append(req["method"])
        method = req.get("method")
        rid = req.get("id")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fake"}}}
        if method == "notifications/initialized":
            return {}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}
        if method == "tools/call":
            p = req.get("params", {})
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text",
                                            "text": f"{p.get('name')}({p.get('arguments')})"}]}}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no method"}}

    return transport


# ── the runtime lifecycle ─────────────────────────────────────────────────────
async def test_connect_then_discover_builds_specs():
    rt = MCPToolRuntime({"name": "wx"}, transport=fake_mcp([WEATHER_TOOL]))
    assert await rt.connect() is True
    assert rt.connected and rt.error is None
    specs = await rt.discover()
    assert {s["name"] for s in specs} == {"weather"}
    # inputSchema → input_schema (Anthropic-compatible)
    assert specs[0]["input_schema"]["properties"]["city"]["type"] == "string"


async def test_resolve_is_idempotent():
    calls: list[str] = []
    rt = MCPToolRuntime({"name": "wx"}, transport=fake_mcp([WEATHER_TOOL], record=calls))
    await rt.resolve()
    await rt.resolve()
    assert calls.count("initialize") == 1  # connect+discover ran exactly once
    assert calls.count("tools/list") == 1


async def test_call_tool_roundtrips():
    rt = MCPToolRuntime({"name": "wx"}, transport=fake_mcp([WEATHER_TOOL]))
    await rt.resolve()
    out = await rt.call_tool("weather", {"city": "hanoi"})
    assert "weather" in out and "hanoi" in out


async def test_failed_connect_degrades_gracefully():
    def boom(req):
        raise RuntimeError("server down")

    rt = MCPToolRuntime({"name": "down"}, transport=boom)
    assert await rt.resolve() is False
    assert rt.connected is False and rt.error and "server down" in rt.error
    assert rt.get_tool_specs() == []  # no tools registered from a dead server
    out = await rt.call_tool("weather", {"city": "x"})
    assert "not connected" in out


def test_specs_empty_before_resolve():
    rt = MCPToolRuntime({"name": "wx"}, transport=fake_mcp([WEATHER_TOOL]))
    assert rt.get_tool_specs() == []  # nothing registered until the resolve phase runs


# ── connection status taxonomy (why a server is/isn't contributing tools) ──────
async def test_status_connected_after_resolve():
    rt = MCPToolRuntime({"name": "wx"}, transport=fake_mcp([WEATHER_TOOL]))
    assert rt.status == "unreachable"  # before resolve, not yet probed
    await rt.resolve()
    assert rt.status == "connected"


def test_status_unconfigured_without_endpoint_or_transport():
    rt = MCPToolRuntime({"name": "wx"})  # no endpoint, no transport
    assert rt.status == "unconfigured"


async def test_status_timeout_classified():
    def slow(req):
        raise TimeoutError("no response")

    rt = MCPToolRuntime({"name": "down"}, transport=slow)
    await rt.resolve()
    assert rt.status == "timeout" and rt.connected is False


async def test_status_unauthorized_classified():
    def deny(req):
        raise RuntimeError("HTTP 401 unauthorized")

    rt = MCPToolRuntime({"name": "wx"}, transport=deny)
    await rt.resolve()
    assert rt.status == "unauthorized"


async def test_status_unreachable_classified():
    def boom(req):
        raise ConnectionError("connection refused")

    rt = MCPToolRuntime({"name": "wx"}, transport=boom)
    await rt.resolve()
    assert rt.status == "unreachable"


async def test_status_bad_response_when_tools_list_errors():
    def half_up(req):
        # initialize succeeds, tools/list returns a JSON-RPC error → bad_response
        if req.get("method") in ("initialize", "notifications/initialized"):
            return {"jsonrpc": "2.0", "id": req.get("id"),
                    "result": {"protocolVersion": "2025-06-18", "serverInfo": {}}}
        return {"jsonrpc": "2.0", "id": req.get("id"),
                "error": {"code": -32000, "message": "boom"}}

    rt = MCPToolRuntime({"name": "wx"}, transport=half_up)
    await rt.resolve()
    assert rt.connected is True and rt.status == "bad_response" and rt.get_tool_specs() == []


# ── the agent resolve phase wires discovered tools into the runtime ────────────
async def test_agent_connect_registers_mcp_tools():
    agent = PreactAgent(
        client=FakeClient(),
        plugins=[PluginMCP(spec={"name": "wx", "transport": "embedded"},
                           transport=fake_mcp([WEATHER_TOOL]))],
    )
    status = await agent.connect()
    assert status == {"wx": True}
    names = {s["name"] for s in agent.engine.tools.get_tool_specs()}
    assert "weather" in names
    # callable through the composite the engine uses
    out = await agent.engine.tools.call_tool("weather", {"city": "hanoi"}, [], set())
    assert "weather" in out and "hanoi" in out


async def test_mcp_resolves_lazily_on_first_turn():
    agent = PreactAgent(
        client=FakeClient(default="ok"),
        mcp_servers=[MCPToolRuntime({"name": "wx"}, transport=fake_mcp([WEATHER_TOOL]))],
    )
    assert agent._mcp_resolved is False
    await agent.query("hello")  # the turn runs the resolve phase first
    assert agent._mcp_resolved is True
    assert "weather" in {s["name"] for s in agent.engine.tools.get_tool_specs()}


async def test_dead_mcp_server_does_not_block_the_turn():
    def boom(req):
        raise RuntimeError("down")

    agent = PreactAgent(
        client=FakeClient(default="answer"),
        mcp_servers=[MCPToolRuntime({"name": "down"}, transport=boom)],
    )
    result = await agent.query("hello")  # must still answer, just without the MCP tools
    assert result.text
    assert await agent.connect() == {"down": False}


def test_no_mcp_is_parity():
    agent = PreactAgent(client=FakeClient())
    assert agent._mcp_runtimes == []


FORECAST_TOOL = {
    "name": "forecast",
    "description": "7-day forecast for a city.",
    "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}},
}


async def test_one_plugin_owns_many_mcp_servers():
    """A single plugin declaring TWO dedicated MCP servers — no need for 2 (or 3) plugins.
    Both resolve and all their discovered tools register under the one plugin."""

    class WeatherSuite:
        name = "weather_suite"

        def __init__(self):
            # declarative: the plugin OWNS its MCP servers (one or many)
            self.mcp_servers = [
                MCPToolRuntime({"name": "current"}, transport=fake_mcp([WEATHER_TOOL])),
                MCPToolRuntime({"name": "forecast"}, transport=fake_mcp([FORECAST_TOOL])),
            ]

        def install(self, setup):
            return None  # nothing imperative needed — MCPs are declared

    agent = PreactAgent(client=FakeClient(), plugins=[WeatherSuite()])
    assert await agent.connect() == {"current": True, "forecast": True}
    names = {s["name"] for s in agent.engine.tools.get_tool_specs()}
    assert {"weather", "forecast"} <= names
    assert "hanoi" in await agent.engine.tools.call_tool("forecast", {"city": "hanoi"}, [], set())


async def test_plugin_can_also_add_mcp_imperatively():
    """The imperative seam still works inside install() — for dynamic/conditional servers."""

    class Dynamic:
        name = "dyn"

        def install(self, setup):
            setup.add_mcp_server({"name": "wx"}, transport=fake_mcp([WEATHER_TOOL]))

    agent = PreactAgent(client=FakeClient(), plugins=[Dynamic()])
    assert await agent.connect() == {"wx": True}
    assert "weather" in {s["name"] for s in agent.engine.tools.get_tool_specs()}
