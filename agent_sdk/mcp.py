"""MCP client + tool runtime — connect, check status, discover schema, register.

When an MCP server config is passed in (``PluginMCP(spec=…)`` /
``AgentSetup.add_mcp_server`` / ``PreactAgent(mcp_servers=[…])``), the agent runs a **resolve
phase** before the first turn:

1. **connect / status** — JSON-RPC ``initialize`` handshake; a server that doesn't answer is
   marked not-connected and its error recorded (no crash).
2. **discover schema** — if connected, ``tools/list`` returns the server's tools.
3. **build tool specs** — each tool becomes an Anthropic-compatible spec (``input_schema``).
4. **register downstream** — the specs flow through the normal ``CompositeToolRuntime``, so the
   discovered tools are offered to the model and ``tools/call`` executes them — exactly like any
   other tool. No tool registration code downstream needs to know it came from MCP.

Transport is pluggable: the default talks JSON-RPC over HTTP (``httpx``); an *embedded*
in-process server (or a test double) is wired by passing ``transport=<async req→resp>``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = ["MCPServerSpec", "MCPToolRuntime", "MCPError", "ConnectionStatus"]

# Why a server is (not) contributing tools this turn — a richer signal than a bare
# bool, so a host can record the reason in ``trace.degraded`` or a "test connection"
# UI. ``connected`` ⇒ handshake + schema OK; the rest classify the failure.
#   unauthorized — handshake rejected for auth (401/403/auth error)
#   unreachable  — connection refused / DNS / network error
#   timeout      — no response within the deadline
#   bad_response — reachable but the reply wasn't valid JSON-RPC / had no tools
#   unconfigured — the spec has no endpoint to probe
ConnectionStatus = Literal[
    "connected",
    "unauthorized",
    "unreachable",
    "timeout",
    "bad_response",
    "unconfigured",
]


def _classify_error(exc: Exception) -> ConnectionStatus:
    """Map a connect/discover exception onto a :data:`ConnectionStatus`."""
    name = type(exc).__name__
    text = f"{name}: {exc}".lower()
    if "timeout" in name.lower() or "timeout" in text:
        return "timeout"
    if any(t in text for t in ("401", "403", "unauthor", "forbidden", "auth")):
        return "unauthorized"
    if isinstance(exc, MCPError):
        return "bad_response"
    return "unreachable"

_JSONRPC = "2.0"
_PROTOCOL_VERSION = "2025-06-18"  # MCP protocol revision the client advertises
_CLIENT_INFO = {"name": "agent-sdk", "version": "0.1.0"}

# A transport is an async (or sync) callable: a JSON-RPC request dict → response dict.
Transport = Callable[[dict], Awaitable[dict] | dict]


class MCPError(Exception):
    """A JSON-RPC error returned by an MCP server."""


@dataclass
class MCPServerSpec:
    """A declarative MCP server definition (pure data — serializable)."""

    name: str
    transport: str = "http"  # http | sse | embedded
    endpoint: str = ""  # URL for http/sse
    auth_type: str = ""  # "" | bearer | header
    auth: str = ""  # token (bearer) or "Header-Name: value" (header)
    kind: str = ""  # capabilities.kind — parity with agent-core gating
    config: dict = field(default_factory=dict)

    @classmethod
    def from_obj(cls, obj: Any) -> MCPServerSpec:
        if isinstance(obj, cls):
            return obj
        d = dict(obj or {})
        caps = d.get("capabilities") or {}
        return cls(
            name=str(d.get("name") or d.get("mcp_server_ref") or ""),
            transport=str(d.get("transport") or "http"),
            endpoint=str(d.get("endpoint") or d.get("url") or ""),
            auth_type=str(d.get("auth_type") or ""),
            auth=str(d.get("auth") or d.get("token") or ""),
            kind=str(d.get("kind") or (caps.get("kind") if isinstance(caps, dict) else "") or ""),
            config=dict(d.get("config") or {}),
        )


def _render_content(result: dict) -> str:
    """An MCP ``tools/call`` result → model-visible text."""
    content = (result or {}).get("content") or []
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and (block.get("type") == "text" or "text" in block):
            parts.append(str(block.get("text", "")))
    text = "\n".join(p for p in parts if p) or str(result or "")
    return f"Error: {text}" if (result or {}).get("isError") else text


class MCPToolRuntime:
    """A ``ToolRuntime`` backed by a remote MCP server, resolved lazily (connect+discover).

    Until :meth:`resolve` succeeds, ``get_tool_specs`` returns ``[]`` — a server that never
    connects contributes no tools (graceful degrade), with ``.error`` set for inspection.
    """

    def __init__(self, spec: Any, *, transport: Transport | None = None):
        self.spec = MCPServerSpec.from_obj(spec)
        self._transport = transport
        self.connected = False
        # A transport-backed runtime (embedded / test double) needs no endpoint.
        _configured = bool(self.spec.endpoint) or self._transport is not None
        self.status: ConnectionStatus = "unreachable" if _configured else "unconfigured"
        self.error: str | None = None
        self.server_info: dict = {}
        self._specs: list[dict] = []
        self._resolved = False
        self._id = 0

    @property
    def name(self) -> str:
        return self.spec.name

    # ── JSON-RPC plumbing ─────────────────────────────────────────────────────
    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def _send(self, req: dict) -> dict:
        if self._transport is not None:
            out = self._transport(req)
            return (await out) if hasattr(out, "__await__") else out  # type: ignore[return-value]
        return await self._http_send(req)

    async def _http_send(self, req: dict) -> dict:
        import httpx

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.spec.auth_type == "bearer" and self.spec.auth:
            headers["Authorization"] = f"Bearer {self.spec.auth}"
        elif self.spec.auth_type == "header" and ":" in self.spec.auth:
            k, _, v = self.spec.auth.partition(":")
            headers[k.strip()] = v.strip()
        if not self.spec.endpoint:
            raise MCPError({"message": f"MCP server {self.spec.name!r} has no endpoint"})
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(self.spec.endpoint, json=req, headers=headers)
            r.raise_for_status()
            return r.json() if r.content else {}

    async def _rpc(self, method: str, params: dict | None = None, *, notify: bool = False) -> dict:
        req: dict = {"jsonrpc": _JSONRPC, "method": method}
        if not notify:
            req["id"] = self._next_id()
        if params is not None:
            req["params"] = params
        resp = await self._send(req)
        if notify:
            return {}
        if isinstance(resp, dict) and resp.get("error"):
            raise MCPError(resp["error"])
        return (resp or {}).get("result", {}) if isinstance(resp, dict) else {}

    # ── lifecycle: connect → discover → resolve ───────────────────────────────
    async def connect(self) -> bool:
        """Handshake (``initialize``) and record status. Never raises."""
        try:
            result = await self._rpc(
                "initialize",
                {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {}, "clientInfo": _CLIENT_INFO},
            )
            self.server_info = (result or {}).get("serverInfo", {})
            await self._rpc("notifications/initialized", notify=True)
            self.connected = True
            self.status = "connected"
            self.error = None
        except Exception as exc:  # connection/handshake failure → degrade, don't crash the turn
            self.connected = False
            self.status = _classify_error(exc)
            self.error = f"{type(exc).__name__}: {exc}"
        return self.connected

    async def discover(self) -> list[dict]:
        """``tools/list`` → Anthropic-compatible specs. Requires a prior connect."""
        if not self.connected:
            return []
        try:
            result = await self._rpc("tools/list")
            tools = (result or {}).get("tools", []) or []
            self._specs = [self._to_spec(t) for t in tools if isinstance(t, dict) and t.get("name")]
        except Exception as exc:
            self.status = "bad_response"
            self.error = f"{type(exc).__name__}: {exc}"
            self._specs = []
        return self._specs

    async def resolve(self) -> bool:
        """The resolve phase: connect (status), then discover the schema if up. Idempotent."""
        if self._resolved:
            return self.connected
        await self.connect()
        if self.connected:
            await self.discover()
        self._resolved = True
        return self.connected

    @staticmethod
    def _to_spec(t: dict) -> dict:
        return {
            "name": str(t["name"]),
            "description": str(t.get("description", "")),
            "input_schema": t.get("inputSchema")
            or t.get("input_schema")
            or {"type": "object", "properties": {}},
        }

    # ── ToolRuntime surface ───────────────────────────────────────────────────
    def get_tool_specs(self) -> list[dict]:
        return list(self._specs)

    async def call_tool(
        self,
        name: str,
        inp: dict,
        retrieved_chunks: list[dict] | None = None,
        already_read: set[str] | None = None,
    ) -> str:
        if not self.connected:
            return f"Error: MCP server {self.spec.name!r} is not connected ({self.error or 'no status'})."
        try:
            result = await self._rpc("tools/call", {"name": name, "arguments": inp or {}})
        except Exception as exc:
            return f"Error: MCP tool {name!r} failed: {exc}"
        return _render_content(result)
