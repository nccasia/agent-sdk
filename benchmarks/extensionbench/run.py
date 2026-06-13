#!/usr/bin/env python3
"""extensionbench — the LIVE bench for *plugins as first-class plug-and-play components*.

Like agentbench, this is a LIVE bench: a real ``PreactAgent`` driven against a real provider,
**no LLM stubs, no FakeClient**. It measures what plugging actually changes in the agent's
behavior, across two scenario kinds:

  - ``plugin`` — a full-surface plugin (``PluginSupportTriage``): an urgent-ticket turn routes to
    the plugin's flow, lights its lobe, and the model calls its local tool; unplugged it's gone.
  - ``mcp`` — a plugin that OWNS a dedicated MCP server (``OrdersPlugin``): the agent connects to
    the MCP (status check), discovers its schema, registers the tool, routes to the plugin's
    agentic flow, and the live model CALLS the MCP-served tool; unplugged none of it exists.
    (The MCP server runs in-process over the transport seam — a real MCP server; the agent /
    provider is live.)

The deterministic plug/unplug *structure* (which objects land in the engine, the MCP
connect→discover lifecycle, the pinned-lobe guard, no-plugin parity) is in the unit suite —
``tests/test_plugins_full_surface.py`` + ``tests/test_mcp.py`` — not a bench.

    python run.py --live              # run the behaviors, print the scorecard
    python run.py --live --report     # also write the probe-trace HTML report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SDK_ROOT = HERE.parents[1]
sys.path.insert(0, str(SDK_ROOT))

from agent_sdk import MCPToolRuntime, PreactAgent, flow, probe, stage, write_viewer  # noqa: E402
from agent_sdk.clients import make_client  # noqa: E402
from agent_sdk.lobes.runtime import Lobe  # noqa: E402
from agent_sdk.network.activation import LAYER_COGNITION  # noqa: E402
from agent_sdk.plugins import PluginSupportTriage  # noqa: E402
from benchmarks._shared import compose_verdict, load_provider  # noqa: E402

DATASET = HERE / "dataset"
_SUPPORT_INSTR = ("You are a support assistant. When a ticket or order id is mentioned, look it "
                  "up with the available tool before answering, and state the result.")


def _jsonl(name: str) -> list[dict]:
    return [json.loads(x) for x in (DATASET / name).read_text().splitlines() if x.strip()]


def _tool_names(rec) -> set[str]:
    return {str(c.get("name")) for c in rec.tool_calls}


# ── an in-process MCP server a plugin owns (real protocol over the transport seam) ──────────
def _orders_mcp_transport():
    tool = {
        "name": "order_status",
        "description": "Look up the current shipping status of an order by its id.",
        "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"}},
                        "required": ["order_id"]},
    }

    def transport(req: dict) -> dict:
        method, rid = req.get("method"), req.get("id")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "orders"}}}
        if method == "notifications/initialized":
            return {}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [tool]}}
        if method == "tools/call":
            oid = (req.get("params", {}).get("arguments") or {}).get("order_id", "?")
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text",
                                            "text": f"Order {oid}: shipped, arriving Friday."}]}}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no method"}}

    return transport


class _OrdersLobe(Lobe):
    id = "order_lookup"
    name = "Order lookup"
    description = "Frame the turn as an order-status lookup."
    use_when = "a question about an order's shipping status"
    layer = LAYER_COGNITION
    behavior = "select"
    system_prompt = ("Look up the order with the order_status tool, then report its status "
                     "concisely.")

    def activation(self, ctx: dict) -> float:
        q = str(ctx.get("query", "")).lower()
        return 1.0 if ("order" in q or "ord-" in q) else 0.0


class OrdersPlugin:
    """A plugin that OWNS a dedicated MCP server (``orders``) plus an agentic flow that uses its
    discovered tool — the canonical 'plugin with its own MCP' shape."""

    name = "orders_mcp"

    def __init__(self):
        # declarative: this plugin owns its MCP server (could be many)
        self.mcp_servers = [MCPToolRuntime({"name": "orders"}, transport=_orders_mcp_transport())]

    def install(self, setup) -> None:
        setup.add_lobe(_OrdersLobe())
        setup.add_stage(stage("order_lookup", lobes=["order_lookup"], loop="agentic",
                              tools=["order_status"],
                              description="Look up the order via order_status and report it."))
        setup.add_flow(flow("orders", use_when="an order shipping-status question",
                            stages=["order_lookup"], threshold=0.5,
                            signal={"any": [{"lexical": ["order", "ord-", "shipment", "delivery"]}]}))


# ── scenarios ────────────────────────────────────────────────────────────────────────────────
async def _plugin_scenario(b: dict, model) -> tuple[list[dict], list]:
    q = b["query"]
    plugged = await probe(PreactAgent(client=make_client(model), instructions=_SUPPORT_INSTR,
                                      plugins=[PluginSupportTriage()]), q, label=f"plugged · {b['id']}")
    bare = await probe(PreactAgent(client=make_client(model), instructions=_SUPPORT_INSTR),
                       q, label=f"unplugged · {b['id']}")
    tri, tool, lobe = b["triage_path"], b["triage_tool"], b["triage_lobe"]
    checks = [
        {"id": "plugin.path_active", "ok": plugged.flow == tri,
         "detail": f"flow={plugged.flow!r}"},
        {"id": "plugin.lobe_active", "ok": lobe in plugged.activated_lobes,
         "detail": f"lobes={plugged.activated_lobes}"},
        {"id": "plugin.tool_active", "ok": tool in _tool_names(plugged),
         "detail": f"tools={sorted(_tool_names(plugged))}"},
        {"id": "unplugged.tool_gone", "ok": tool not in _tool_names(bare),
         "detail": f"tools={sorted(_tool_names(bare))}"},
        {"id": "unplugged.path_gone", "ok": bare.flow != tri,
         "detail": f"flow={bare.flow!r}"},
    ]
    return checks, [plugged, bare]


async def _mcp_scenario(b: dict, model) -> tuple[list[dict], list]:
    q = b["query"]
    tool, server, path = b["mcp_tool"], b["mcp_server"], b["mcp_path"]
    plugged_agent = PreactAgent(client=make_client(model), instructions=_SUPPORT_INSTR,
                                plugins=[OrdersPlugin()])
    status = await plugged_agent.connect()  # connect + discover the owned MCP server
    discovered = {s["name"] for s in plugged_agent.engine.tools.get_tool_specs()}
    plugged = await probe(plugged_agent, q, label=f"plugged(mcp) · {b['id']}")
    bare = await probe(PreactAgent(client=make_client(model), instructions=_SUPPORT_INSTR),
                       q, label=f"unplugged · {b['id']}")
    checks = [
        {"id": "mcp.connected", "ok": status.get(server) is True, "detail": f"status={status}"},
        {"id": "mcp.tool_discovered", "ok": tool in discovered,
         "detail": f"discovered={sorted(discovered)}"},
        {"id": "mcp.path_active", "ok": plugged.flow == path, "detail": f"flow={plugged.flow!r}"},
        {"id": "mcp.tool_called", "ok": tool in _tool_names(plugged),
         "detail": f"tools={sorted(_tool_names(plugged))}"},
        {"id": "mcp.unplugged_gone", "ok": tool not in _tool_names(bare),
         "detail": f"tools={sorted(_tool_names(bare))}"},
    ]
    return checks, [plugged, bare]


async def main() -> int:
    ap = argparse.ArgumentParser(description="extensionbench — plugin plug-and-play LIVE bench")
    ap.add_argument("--live", action="store_true", help="run live (real provider calls)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--report", action="store_true", help="write the probe-trace HTML report")
    args = ap.parse_args()

    if not args.live:
        print("extensionbench only runs live. Pass --live (it makes real provider calls).",
              file=sys.stderr)
        return 2

    model = args.model or load_provider()
    if model is None:
        print("extensionbench is a LIVE bench — set a provider token in packages/agent-sdk/.env "
              "(MINIMAX_API_KEY/MINIMAX_BASE_URL or ANTHROPIC_*).", file=sys.stderr)
        return 2

    print(f"[extensionbench] live · model={model}\n")
    checks: list[dict] = []
    probes: list = []
    for b in _jsonl("behaviors.jsonl"):
        handler = _mcp_scenario if b.get("kind") == "mcp" else _plugin_scenario
        cks, prs = await handler(b, model)
        checks += cks
        probes += prs

    payload = {"all_pass": all(c["ok"] for c in checks), "checks": checks}
    verdict = compose_verdict({"extension": payload})

    print("── extensionbench ─────────────────────────────────────────────")
    for c in checks:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['id']:<24} {c['detail']}")
    print(f"\nextensionbench: {sum(c['ok'] for c in checks)}/{len(checks)} behaviors pass · "
          f"verdict {verdict['status']}")
    if verdict["reasons"]:
        print("reasons:", "; ".join(verdict["reasons"]))

    if args.report:
        html = write_viewer(HERE / "results" / "extensionbench.html", probes,
                            label="extensionbench · plugin + MCP plug-and-play")
        print(f"report: {html}")

    return 0 if verdict["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
