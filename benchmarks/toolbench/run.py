#!/usr/bin/env python3
"""toolbench — the bench for the SDK's TOOL-USE concepts (ported from the monorepo toolbench,
re-authored on the agent-sdk public surface; leaf-pure).

Certifies the machinery a PreAct agent uses to define, expose, route, and run tools at scale:
``@tool`` spec generation, the ``FunctionToolRuntime`` / ``CompositeToolRuntime`` boundary, embedded
``MCPToolRuntime`` discovery, the ``ToolSelectLobe`` adaptive-exposure algorithm, and the live
agentic ``tool_loop``. Free deterministic modes run with no provider; the live mode drives a real
``PreactAgent``. See ``METHOD.md`` for the optimization levers + gates.

    python benchmarks/toolbench/run.py                  # free tier only (no provider)
    python benchmarks/toolbench/run.py --live --report  # + the live agentic loop, write the viewer
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SDK_ROOT = HERE.parents[1]
sys.path.insert(0, str(SDK_ROOT))

from pydantic import BaseModel  # noqa: E402

from agent_sdk import (  # noqa: E402
    CompositeToolRuntime,
    FunctionToolRuntime,
    MCPToolRuntime,
    PreactAgent,
    flow,
    probe,
    stage,
    tool,
    write_viewer,
)
from agent_sdk.clients import make_client  # noqa: E402
from agent_sdk.lobes.runtime import Lobe  # noqa: E402
from agent_sdk.network.activation import LAYER_COGNITION  # noqa: E402
from agent_sdk.network.context_builder import DEFAULT_NODE_WEIGHTS  # noqa: E402
from agent_sdk.tools.lobes.tool_select import ToolSelectLobe  # noqa: E402
from benchmarks._shared import compose_verdict, load_provider  # noqa: E402

RESULTS = HERE / "results"


# ── demo tools (the @tool concept: docstring → description, signature → schema) ───────────────
@tool
def get_weather(city: str, units: str = "celsius") -> str:
    "Report the current weather for a city."
    return f"{city}: 21 {units}"


class Ticket(BaseModel):
    title: str
    priority: int = 3


@tool(name="tickets.create", requires=["acl"])
def create_ticket(args: Ticket) -> str:
    "Open a support ticket."
    return f"created {args.title!r} p{args.priority}"


@tool
def order_status_local(order_id: str) -> str:
    "Look up the shipping status of an order by id."
    return f"Order {order_id}: shipped, arriving Friday."


# ── an in-process (embedded) MCP server — a real protocol over the transport seam, no network ──
def _orders_mcp_transport():
    spec = {"name": "order_status", "description": "Shipping status of an order by id.",
            "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"}},
                            "required": ["order_id"]}}

    def transport(req: dict) -> dict:
        method, rid = req.get("method"), req.get("id")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "orders"}}}
        if method == "notifications/initialized":
            return {}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [spec]}}
        if method == "tools/call":
            oid = (req.get("params", {}).get("arguments") or {}).get("order_id", "?")
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": f"Order {oid}: in transit."}]}}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no method"}}

    return transport


def _ck(cid: str, ok: bool, detail: str) -> dict:
    return {"id": cid, "ok": bool(ok), "detail": detail}


def _payload(checks: list[dict], metrics: dict | None = None) -> dict:
    return {"checks": checks, "n": len(checks), "pass": sum(c["ok"] for c in checks),
            "all_pass": all(c["ok"] for c in checks) and bool(checks), "metrics": metrics or {}}


# ── free: @tool spec generation + validation + invocation ─────────────────────────────────────
async def run_spec() -> dict:
    s = get_weather.spec
    props = (s.get("input_schema") or {}).get("properties") or {}
    rt = FunctionToolRuntime([get_weather])
    invoked = await rt.call_tool("get_weather", {"city": "Hanoi"})
    missing = await rt.call_tool("get_weather", {})        # model-actionable error, not a traceback
    unknown = await rt.call_tool("nope", {})
    checks = [
        _ck("spec.wellformed", s["name"] == "get_weather" and s["description"].startswith("Report")
            and "city" in props and "units" in props, f"props={sorted(props)}"),
        _ck("spec.required_inference", get_weather.missing_required({}) == ["city"]
            and get_weather.missing_required({"city": "x"}) == [], "city required, units optional"),
        _ck("spec.pydantic_schema", (create_ticket.input_schema.get("type") == "object")
            and "title" in (create_ticket.input_schema.get("properties") or {})
            and create_ticket.missing_required({}) == [], "BaseModel arg → model schema"),
        _ck("spec.requires_captured", create_ticket.requires == ("acl",), f"{create_ticket.requires}"),
        _ck("spec.invoke_stringifies", invoked == "Hanoi: 21 celsius"
            and missing.startswith("Error") and unknown.startswith("Error"),
            f"invoke={invoked!r}; missing-arg & unknown-tool → clean errors"),
    ]
    return _payload(checks)


# ── free: ToolSelectLobe adaptive exposure (essentials firewall + relevance trim) ──────────────
def _catalog() -> list[dict]:
    rows = [
        ("kb.search", "Search the knowledge base for documents."),                       # essential
        ("memory", "Save or recall durable memory entries."),                            # essential
        ("search_docs", "Search internal documentation and knowledge base articles about refunds and policies."),
        ("delete_account", "Permanently delete a user account."),
        ("send_email", "Send an email to a recipient."),
        ("convert_currency", "Convert an amount between two currencies."),
        ("schedule_meeting", "Schedule a calendar meeting with attendees."),
        ("resize_image", "Resize an image file to given dimensions."),
        ("translate_text", "Translate text from one language to another."),
        ("roll_dice", "Roll an n-sided die and return the result."),
    ]
    return [{"name": n, "description": d} for n, d in rows]


async def run_select() -> dict:
    lobe = ToolSelectLobe()
    specs = _catalog()
    essential = lambda n: n.startswith("kb.") or n == "memory"  # noqa: E731
    kept, rec = lobe.select(
        specs, query="find knowledge base articles about refunds", q_vec=None, embed_one=None,
        essential=essential, weights=DEFAULT_NODE_WEIGHTS, min_activation=0.05, max_tools=4)
    kept_names = {k["name"] for k in rec["kept"]}
    dropped = {d["name"]: d["reason"] for d in rec["dropped"]}
    non_ess_kept = sum(1 for k in rec["kept"] if not k.get("essential"))
    checks = [
        _ck("select.essentials_kept", {"kb.search", "memory"} <= kept_names, f"kept={sorted(kept_names)}"),
        _ck("select.relevant_kept", "search_docs" in kept_names, "the on-topic tool survives the trim"),
        _ck("select.irrelevant_dropped", dropped.get("delete_account") == "below_floor",
            f"dropped={dropped}"),
        _ck("select.budget_respected", non_ess_kept <= 4 - 2, f"non-essential kept={non_ess_kept} ≤ 2"),
        _ck("select.parity_dark", lobe.activation({}) == 0.0
            and lobe.activation({"tool_strategy": "adaptive"}) == 1.0,
            "inert unless tool_strategy=adaptive (byte-identical at parity)"),
    ]
    return _payload(checks, {"tools_dropped": len(dropped), "kept": len(kept)})


# ── free: CompositeToolRuntime dispatch + embedded MCP discovery (no provider) ─────────────────
async def run_composite() -> dict:
    func_rt = FunctionToolRuntime([get_weather, order_status_local])
    mcp = MCPToolRuntime({"name": "orders"}, transport=_orders_mcp_transport())
    await mcp.resolve()                                    # connect → discover, in-process
    mcp_names = {s.get("name") for s in mcp.get_tool_specs()}
    comp = CompositeToolRuntime([func_rt, mcp])
    all_names = {s.get("name") for s in comp.get_tool_specs()}
    routed_fn = await comp.call_tool("get_weather", {"city": "Hanoi"}, [], set())
    routed_mcp = await comp.call_tool("order_status", {"order_id": "A1"}, [], set())
    checks = [
        _ck("composite.mcp_discovered", "order_status" in mcp_names, f"mcp tools={sorted(mcp_names)}"),
        _ck("composite.mcp_called", "A1" in routed_mcp, f"call→{routed_mcp!r}"),
        _ck("composite.routing", routed_fn == "Hanoi: 21 celsius"
            and {"get_weather", "order_status"} <= all_names, f"specs={sorted(all_names)}"),
        _ck("composite.external_marked", "order_status" in comp.external_names(),
            "external MCP tools are never scored out by adaptive selection"),
    ]
    return _payload(checks)


# ── live: the agentic tool_loop runs, feeds results back, terminates bounded ───────────────────
class _LookupLobe(Lobe):
    id = "tool_lookup"
    name = "Tool lookup"
    description = "Frame the turn as a lookup that needs a tool call."
    use_when = "a question answerable only by calling a tool"
    layer = LAYER_COGNITION
    behavior = "select"
    system_prompt = "Call the available tool to look up the answer, then state the result concisely."

    def activation(self, ctx: dict) -> float:
        q = str(ctx.get("query", "")).lower()
        return 1.0 if ("weather" in q or "order" in q) else 0.0


class _ToolsPlugin:
    name = "toolbench_tools"

    def install(self, setup) -> None:
        setup.add_lobe(_LookupLobe())
        setup.add_stage(stage("tool_lookup", lobes=["tool_lookup"], loop="agentic",
                              tools=["get_weather", "order_status_local"],
                              description="Look up the answer with a tool and report it."))
        setup.add_flow(flow("lookup", use_when="a tool-lookup question", stages=["tool_lookup"],
                            threshold=0.5, signal={"any": [{"lexical": ["weather", "order"]}]}))


async def run_loop(model: str) -> tuple[dict, list]:
    agent = PreactAgent(client=make_client(model), tools=[get_weather, order_status_local],
                        plugins=[_ToolsPlugin()],
                        instructions="You answer by calling tools, then reporting the result.")
    rec = await probe(agent, "What is the weather in Hanoi right now?", label="loop · weather")
    called = {str(c.get("name")) for c in rec.tool_calls}
    hops = len(rec.tool_calls)
    checks = [
        _ck("loop.tool_called", "get_weather" in called, f"tools={sorted(called)}"),
        _ck("loop.terminated", rec.status in ("ok", "answered") and bool(rec.answer),
            f"status={rec.status}"),
        _ck("loop.bounded", 0 < hops <= 6, f"hops={hops}"),
    ]
    return _payload(checks, {"hops": hops}), [rec]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="also run the live agentic-loop mode")
    ap.add_argument("--report", action="store_true", help="write results/toolbench.html")
    ap.add_argument("--label", default="base")
    ap.add_argument("--model", default=None)
    ap.add_argument("--trials", type=int, default=1)  # reserved (live variance pooling)
    args = ap.parse_args()

    payloads: dict[str, dict | None] = {
        "spec": await run_spec(),
        "select": await run_select(),
        "composite": await run_composite(),
    }
    probes: list = []
    if args.live:
        model = args.model or load_provider()
        if model is None:
            print("toolbench live tier needs a provider token — set one in packages/agent-sdk/.env "
                  "(MINIMAX_API_KEY/MINIMAX_BASE_URL or ANTHROPIC_*).", file=sys.stderr)
            payloads["loop"] = None  # → UNMEASURED
        else:
            print(f"[toolbench] live · model={model}")
            payloads["loop"], probes = await run_loop(model)

    verdict = compose_verdict(payloads, record={"select": ["tools_dropped"], "loop": ["hops"]})

    print("── toolbench ──────────────────────────────────────────────────")
    total = ok = 0
    for mode, p in payloads.items():
        if not p:
            print(f"  [····] {mode:<10} UNMEASURED (live tier not run)")
            continue
        for c in p["checks"]:
            print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['id']:<26} {c['detail'][:48]}")
        total += p["n"]
        ok += p["pass"]
    print(f"\ntoolbench: {ok}/{total} checks pass · verdict {verdict['status']}")
    if verdict["reasons"]:
        print("reasons:", "; ".join(verdict["reasons"]))

    if args.report:
        RESULTS.mkdir(exist_ok=True)
        html = write_viewer(RESULTS / "toolbench.html", probes,
                            label="toolbench · SDK tool-use concepts")
        print(f"report: {html}")

    return 0 if verdict["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
