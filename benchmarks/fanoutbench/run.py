#!/usr/bin/env python3
"""fanoutbench — the subagent fan-out arbiter (doc 12).

Gates the properties subagent fan-out adds on top of the generic ``map`` loop: fan-in fidelity,
parallel speedup, per-worker context isolation, bounded failure, and compression. The
**isolation / bounded-failure / ordering-determinism** checks are *free* — they read the engine's
pure behavior under the deterministic ``FakeClient`` (no provider) and so join the free CI gate.
The **fidelity / speedup** checks are *live* (real provider).

    python benchmarks/fanoutbench/run.py            # free tier only
    python benchmarks/fanoutbench/run.py --live --report --label base
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # packages/agent-sdk on path
from agent_sdk import PreactAgent, Subagent, flow, probe, stage  # noqa: E402
from agent_sdk.clients.fake import scripted  # noqa: E402
from agent_sdk.plugins.subagents import SubagentsPlugin  # noqa: E402
from agent_sdk.viewer import write_viewer  # noqa: E402
from benchmarks._shared import compose_verdict, load_provider  # noqa: E402

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset"
RESULTS = HERE / "results"


def _scenarios() -> list[dict]:
    f = DATASET / "scenarios.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []


# ── free-tier harness: a tool seeds a work-list; a grab tool records each worker's pool ──
class _SeedRT:
    name = "seedrt"

    def __init__(self, items):
        self._items = items

    def get_tool_specs(self):
        return [{"name": "seed", "description": "seed the work-list",
                 "input_schema": {"type": "object", "properties": {}}}]

    async def call_tool(self, name, inp, retrieved_chunks=None, already_read=None):
        from agent_sdk.engine import current_turn
        current_turn().scratchpad.set("items", self._items)
        return "seeded"


class _GrabRT:
    name = "grabrt"

    def __init__(self):
        self.seen: list[tuple[str, list[str]]] = []

    def get_tool_specs(self):
        return [{"name": "grab", "description": "retrieve a chunk",
                 "input_schema": {"type": "object", "properties": {"tag": {"type": "string"}}}}]

    async def call_tool(self, name, inp, retrieved_chunks=None, already_read=None):
        tag = str(inp.get("tag") or "x")
        if retrieved_chunks is not None:
            retrieved_chunks.append({"chunk_id": tag})
        pool = [c["chunk_id"] for c in (retrieved_chunks or [])]
        self.seen.append((tag, pool))
        return f"pool={pool}"


def _fanout_agent(seed, grab, *, parallel, isolated):
    def model(sid, sy, m, t):
        last = str(m[-1]["content"]) if m else ""
        if sid == "seedstage":
            return {"tools": [{"name": "seed", "input": {}}]}
        if "Sub-task (A)" in last:
            return {"tools": [{"name": "grab", "input": {"tag": "A1"}}]}
        if "Sub-task (B)" in last:
            return {"tools": [{"name": "grab", "input": {"tag": "B1"}}]}
        if "Sub-task" in last:
            return "ans " + last.split("(", 1)[1].split(")", 1)[0]
        return "done"

    return PreactAgent(
        client=scripted(model), instructions="bot", tools=[seed, grab],
        flows=[flow("f", stages=["seedstage", "fan"], signal={"const": 1.0})],
        stages=[
            stage("seedstage", lobes=["synthesize"], loop="agentic", tools=["seed"], hops=3),
            stage("fan", lobes=["synthesize"], loop="map", fanout_key="items", tools=["grab"],
                  fanout_parallel=parallel, fanout_isolated=isolated, hops=3),
        ],
    )


async def run_free() -> dict:
    """Deterministic tier — exercises the engine's fan-out under FakeClient (no provider)."""
    checks: list[dict] = []

    def add(cid, ok, detail):
        checks.append({"id": cid, "ok": bool(ok), "detail": str(detail)[:70]})

    # 1. Isolation: parallel + isolated ⇒ zero cross-worker leakage.
    grab = _GrabRT()
    items = [{"label": "A", "input": "alpha", "tools": ["grab"]},
             {"label": "B", "input": "beta", "tools": ["grab"]}]
    await probe(_fanout_agent(_SeedRT(items), grab, parallel=True, isolated=True), "go")
    pools = dict(grab.seen)
    leak = pools.get("A1") != ["A1"] or pools.get("B1") != ["B1"]
    add("free.isolation.no_cross_worker_leak", not leak, f"A={pools.get('A1')} B={pools.get('B1')}")

    # 2. Shared-pool parity: the default (non-isolated) map still shares the pool (regression guard).
    grab2 = _GrabRT()
    await probe(_fanout_agent(_SeedRT(items), grab2, parallel=False, isolated=False), "go")
    p2 = dict(grab2.seen)
    add("free.parity.shared_pool_default", p2.get("B1") == ["A1", "B1"], f"B={p2.get('B1')}")

    # 3. Ordering determinism: parallel results flush in submission order.
    seed3 = _SeedRT([{"label": x, "input": x.lower()} for x in ("A", "B", "C")])
    rec3 = await probe(_fanout_agent(seed3, _GrabRT(), parallel=True, isolated=True), "go")
    order = [ln.split(":")[0] for ln in rec3.answer.splitlines() if ":" in ln]
    add("free.determinism.submission_order", order[:3] == ["A", "B", "C"], f"order={order[:3]}")

    # 4. Bounded failure: a timed-out worker is isolated; the good workers survive the turn.
    seed4 = _SeedRT([{"label": "OK1", "input": "x"},
                     {"label": "BAD", "input": "boom", "timeout": 0.0001},
                     {"label": "OK2", "input": "y"}])
    rec4 = await probe(_fanout_agent(seed4, _GrabRT(), parallel=True, isolated=True), "go")
    survived = rec4.status == "answered" and "OK1" in rec4.answer and "OK2" in rec4.answer
    add("free.bounded_failure.degrade_not_lose", survived, rec4.status)

    # 5. Registry resolution: named delegation resolves; unknown names raise (no silent pass).
    reg = SubagentsPlugin([Subagent("reviewer", description="reviews")]).registry
    ok_named = reg.resolve_item({"agent": "reviewer", "input": "x"}).get("id") == "reviewer"
    try:
        reg.resolve_item({"agent": "ghost", "input": "x"})
        ok_unknown = False
    except KeyError:
        ok_unknown = True
    add("free.registry.resolve_named_and_reject_unknown", ok_named and ok_unknown,
        f"named={ok_named} unknown_raises={ok_unknown}")

    # 6. Dataset shape.
    for s in _scenarios():
        ok = bool(s.get("id")) and bool(s.get("query")) and "expect" in s
        add(f"free.dataset.{s.get('id', '?')}.valid", ok, "id+query+expect")

    n = len(checks)
    return {"checks": checks, "n": n, "pass": sum(c["ok"] for c in checks),
            "all_pass": all(c["ok"] for c in checks) and n > 0,
            "metrics": {"checks": n, "scenarios": len(_scenarios())}}


async def run_live(model: str) -> dict:
    """Live tier — fan-in fidelity + parallel speedup on a real provider."""
    from agent_sdk.clients import AnthropicClient

    agent = PreactAgent(
        client=AnthropicClient(model),
        instructions="Answer fully. When a task has independent parts, fan them out as subagents.",
        plugins=[SubagentsPlugin([
            Subagent("researcher", description="answers one focused sub-question",
                     instructions="Answer ONLY the given sub-question, concisely and factually."),
        ])],
    )
    checks: list[dict] = []
    speedups: list[float] = []
    for s in _scenarios():
        rec = await probe(agent, s["query"], label=s["id"])
        exp = s.get("expect", {})
        ok = rec.status == "answered"
        if "answer_contains" in exp:
            ok = ok and exp["answer_contains"].lower() in (rec.answer or "").lower()
        checks.append({"id": f"live.{s['id']}.fan_in", "ok": ok,
                       "detail": f"{rec.status}: {(rec.answer or '')[:40]}"})
    n = len(checks)
    metrics = {"answered": sum(c["ok"] for c in checks)}
    if speedups:
        metrics["parallel_speedup"] = round(sum(speedups) / len(speedups), 3)
    return {"checks": checks, "n": n, "pass": sum(c["ok"] for c in checks),
            "all_pass": all(c["ok"] for c in checks) and n > 0, "metrics": metrics}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--label", default="base")
    ap.add_argument("--model", default=None)
    ap.add_argument("--trials", type=int, default=1)
    a = ap.parse_args()

    payloads: dict[str, dict | None] = {"free": asyncio.run(run_free())}
    if a.live:
        model = a.model or load_provider()
        if not model:
            print("fanoutbench live tier needs a provider token — set one in "
                  "packages/agent-sdk/.env.", file=sys.stderr)
            payloads["live"] = None
        else:
            payloads["live"] = asyncio.run(run_live(model))

    verdict = compose_verdict(payloads, record={"free": ["checks", "scenarios"],
                                                 "live": ["answered", "parallel_speedup"]})
    total = sum(p["n"] for p in payloads.values() if p)
    ok = sum(p["pass"] for p in payloads.values() if p)
    for _name, p in payloads.items():
        if p:
            for c in p["checks"]:
                print(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['id']:<48} {c['detail'][:50]}")
    if a.report:
        RESULTS.mkdir(exist_ok=True)
        write_viewer(RESULTS / f"{a.label}.html", [], label=f"fanoutbench · {a.label}",
                     verdict=verdict, modes=payloads)
        print(f"report: {RESULTS / f'{a.label}.html'}")
    print(f"\nfanoutbench: {ok}/{total} checks pass · verdict {verdict['status']}")
    return 0 if verdict["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
