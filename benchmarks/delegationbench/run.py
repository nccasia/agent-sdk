#!/usr/bin/env python3
"""delegationbench — does the agent delegate well? (metacognition + subagent fan-out, doc 12)

Rich, realistic, multi-faceted queries that a *single* agent answers worse than a *delegating* one
(decompose → fan out scoped subagents → aggregate). It grades two things:

- **The decision (free, deterministic):** does the agent route a query to delegation when — and only
  when — it pays off? Scored on the pure complexity recognizer (`auto_delegate`), so precision (no
  over-delegation on simple queries) and recall (delegate on complex ones) gate with no provider.
- **The execution (live):** end-to-end, does the agent actually fan out on the should-delegate
  cases, cover every facet in the answer (fan-in fidelity), and stay single-shot on the simple ones?

Plus the fan-out engine invariants (isolation / bounded-failure / ordering) carried from the
subagent feature itself.

    python benchmarks/delegationbench/run.py                          # free tier (no provider)
    python benchmarks/delegationbench/run.py --live --report --label base
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # packages/agent-sdk on path
from agent_sdk import PreactAgent, Subagent, flow, probe, stage  # noqa: E402
from agent_sdk.clients.fake import scripted  # noqa: E402
from agent_sdk.plugins.metacognition.path import make_recognize  # noqa: E402
from agent_sdk.plugins.subagents import SubagentsPlugin  # noqa: E402
from agent_sdk.viewer import write_viewer  # noqa: E402
from benchmarks._shared import compose_verdict, load_provider  # noqa: E402

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset"
RESULTS = HERE / "results"

# Gating thresholds (see METHOD.md). Free = the deterministic decision signal; live = the model.
FREE_PRECISION, FREE_RECALL = 0.90, 0.80
LIVE_PRECISION, LIVE_RECALL, LIVE_FIDELITY = 0.80, 0.70, 0.70


def _scenarios() -> list[dict]:
    f = DATASET / "scenarios.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []


def _pr(should: list[bool], did: list[bool]) -> tuple[float, float]:
    """precision, recall of the delegate decision against the labels."""
    tp = sum(1 for s, d in zip(should, did, strict=False) if s and d)
    fp = sum(1 for s, d in zip(should, did, strict=False) if d and not s)
    fn = sum(1 for s, d in zip(should, did, strict=False) if s and not d)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return precision, recall


# ── fan-out engine invariants (deterministic, FakeClient) ─────────────────────
class _SeedRT:
    name = "seedrt"

    def __init__(self, items):
        self._items = items

    def get_tool_specs(self):
        return [
            {
                "name": "seed",
                "description": "seed the work-list",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

    async def call_tool(self, name, inp, retrieved_chunks=None, already_read=None):
        from agent_sdk.engine import current_turn

        current_turn().scratchpad.set("items", self._items)
        return "seeded"


class _GrabRT:
    name = "grabrt"

    def __init__(self):
        self.seen: list[tuple[str, list[str]]] = []

    def get_tool_specs(self):
        return [
            {
                "name": "grab",
                "description": "retrieve a chunk",
                "input_schema": {"type": "object", "properties": {"tag": {"type": "string"}}},
            }
        ]

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
        client=scripted(model),
        instructions="bot",
        tools=[seed, grab],
        flows=[flow("f", stages=["seedstage", "fan"], signal={"const": 1.0})],
        stages=[
            stage("seedstage", lobes=["synthesize"], loop="agentic", tools=["seed"], hops=3),
            stage(
                "fan",
                lobes=["synthesize"],
                loop="map",
                fanout_key="items",
                tools=["grab"],
                fanout_parallel=parallel,
                fanout_isolated=isolated,
                hops=3,
            ),
        ],
    )


async def run_free() -> dict:
    """Deterministic tier — the delegation DECISION + the fan-out engine invariants (no provider)."""
    checks: list[dict] = []

    def add(cid, ok, detail):
        checks.append({"id": cid, "ok": bool(ok), "detail": str(detail)[:70]})

    # ── the delegation decision: complexity recognizer precision/recall over the dataset ──
    recognize = make_recognize(auto_delegate=True)
    scen = _scenarios()
    should, did = [], []
    for s in scen:
        want = bool(s.get("expect", {}).get("delegate"))
        got = recognize({"query": s["query"]}) >= 0.5
        should.append(want)
        did.append(got)
        add(f"free.decision.{s['id']}", got == want, f"want={want} got={got} [{s.get('category')}]")
    precision, recall = _pr(should, did)
    add(
        "free.decision.precision",
        precision >= FREE_PRECISION,
        f"{precision:.2f} >= {FREE_PRECISION}",
    )
    add("free.decision.recall", recall >= FREE_RECALL, f"{recall:.2f} >= {FREE_RECALL}")

    # ── fan-out engine invariants (the subagent feature these scenarios exercise live) ──
    grab = _GrabRT()
    items = [
        {"label": "A", "input": "alpha", "tools": ["grab"]},
        {"label": "B", "input": "beta", "tools": ["grab"]},
    ]
    await probe(_fanout_agent(_SeedRT(items), grab, parallel=True, isolated=True), "go")
    pools = dict(grab.seen)
    add(
        "free.isolation.no_cross_worker_leak",
        pools.get("A1") == ["A1"] and pools.get("B1") == ["B1"],
        f"A={pools.get('A1')} B={pools.get('B1')}",
    )

    seed3 = _SeedRT([{"label": x, "input": x.lower()} for x in ("A", "B", "C")])
    rec3 = await probe(_fanout_agent(seed3, _GrabRT(), parallel=True, isolated=True), "go")
    order = [ln.split(":")[0] for ln in rec3.answer.splitlines() if ":" in ln]
    add("free.determinism.submission_order", order[:3] == ["A", "B", "C"], f"order={order[:3]}")

    seed4 = _SeedRT(
        [
            {"label": "OK1", "input": "x"},
            {"label": "BAD", "input": "boom", "timeout": 0.0001},
            {"label": "OK2", "input": "y"},
        ]
    )
    rec4 = await probe(_fanout_agent(seed4, _GrabRT(), parallel=True, isolated=True), "go")
    add(
        "free.bounded_failure.degrade_not_lose",
        rec4.status == "answered" and "OK1" in rec4.answer and "OK2" in rec4.answer,
        rec4.status,
    )

    n = len(checks)
    return {
        "checks": checks,
        "n": n,
        "pass": sum(c["ok"] for c in checks),
        "all_pass": all(c["ok"] for c in checks) and n > 0,
        "metrics": {
            "decision_precision": round(precision, 3),
            "decision_recall": round(recall, 3),
            "scenarios": len(scen),
        },
    }


# ── live tier: end-to-end delegation behavior on the real provider ────────────
def _delegated(rec) -> int:
    """Subtasks the agent fanned out (0 ⇒ did not delegate). Read from the meta_control call."""
    for tc in rec.tool_calls:
        if tc.get("name") == "meta_control" and (tc.get("input") or {}).get("action") == "fan_out":
            return len(tc["input"].get("items") or []) or 1
    return 0


async def run_live(model: str) -> dict:
    from agent_sdk.clients import AnthropicClient

    agent = PreactAgent(
        client=AnthropicClient(model),
        instructions=(
            "Answer fully and accurately. When a task has several independent parts, "
            "fan them out to subagents and combine their results."
        ),
        plugins=[
            SubagentsPlugin(
                [
                    Subagent(
                        "researcher",
                        description="answers one focused sub-question",
                        instructions="Answer ONLY the given sub-question, concisely and factually.",
                    ),
                ],
                auto_delegate=True,
            )
        ],
    )
    checks: list[dict] = []
    should, did, widths = [], [], []
    fidelity_hits, fidelity_total = 0, 0
    for s in _scenarios():
        exp = s.get("expect", {})
        want = bool(exp.get("delegate"))
        rec = await probe(agent, s["query"], label=s["id"])
        width = _delegated(rec)
        delegated = width > 0
        should.append(want)
        did.append(delegated)
        if delegated:
            widths.append(width)
        # fan-in fidelity on should-delegate cases with a facet contract
        facets = exp.get("answer_contains") or []
        if want and facets:
            fidelity_total += 1
            covered = all(f.lower() in (rec.answer or "").lower() for f in facets)
            if covered:
                fidelity_hits += 1
            checks.append(
                {
                    "id": f"live.fanin.{s['id']}",
                    "ok": covered and rec.status == "answered",
                    "detail": f"delegated={delegated} facets={'all' if covered else 'MISS'}",
                }
            )
        else:
            checks.append(
                {
                    "id": f"live.decision.{s['id']}",
                    "ok": delegated == want,
                    "detail": f"want={want} delegated={delegated} w={width}",
                }
            )

    precision, recall = _pr(should, did)
    fidelity = fidelity_hits / fidelity_total if fidelity_total else 1.0
    checks.extend(
        [
            {
                "id": "live.delegation.precision",
                "ok": precision >= LIVE_PRECISION,
                "detail": f"{precision:.2f} >= {LIVE_PRECISION}",
            },
            {
                "id": "live.delegation.recall",
                "ok": recall >= LIVE_RECALL,
                "detail": f"{recall:.2f} >= {LIVE_RECALL}",
            },
            {
                "id": "live.fanin.fidelity",
                "ok": fidelity >= LIVE_FIDELITY,
                "detail": f"{fidelity:.2f} >= {LIVE_FIDELITY}",
            },
        ]
    )
    n = len(checks)
    return {
        "checks": checks,
        "n": n,
        "pass": sum(c["ok"] for c in checks),
        "all_pass": all(c["ok"] for c in checks) and n > 0,
        "metrics": {
            "delegation_precision": round(precision, 3),
            "delegation_recall": round(recall, 3),
            "fanin_fidelity": round(fidelity, 3),
            "avg_fanout_width": round(sum(widths) / len(widths), 2) if widths else 0,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
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
            print(
                "delegationbench live tier needs a provider token — set one in "
                "packages/agent-sdk/.env.",
                file=sys.stderr,
            )
            payloads["live"] = None
        else:
            payloads["live"] = asyncio.run(run_live(model))

    verdict = compose_verdict(
        payloads,
        record={
            "free": ["decision_precision", "decision_recall", "scenarios"],
            "live": [
                "delegation_precision",
                "delegation_recall",
                "fanin_fidelity",
                "avg_fanout_width",
            ],
        },
    )
    total = sum(p["n"] for p in payloads.values() if p)
    ok = sum(p["pass"] for p in payloads.values() if p)
    for _name, p in payloads.items():
        if p:
            for c in p["checks"]:
                print(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['id']:<40} {c['detail'][:54]}")
    if a.report:
        RESULTS.mkdir(exist_ok=True)
        write_viewer(
            RESULTS / f"{a.label}.html",
            [],
            label=f"delegationbench · {a.label}",
            verdict=verdict,
            modes=payloads,
        )
        print(f"report: {RESULTS / f'{a.label}.html'}")
    print(f"\ndelegationbench: {ok}/{total} checks pass · verdict {verdict['status']}")
    return 0 if verdict["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
