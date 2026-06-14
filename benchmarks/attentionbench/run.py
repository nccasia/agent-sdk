#!/usr/bin/env python3
"""attentionbench — the bench for the SDK's CONTEXT axis (OY: what the agent attends to).

Certifies the two halves of attention: (1) **node selection** — the relevant context outranks
flooders and traps drop below the floor (``score_relevance``); and (2) **lobe activation** — the
right lobes fire per turn (recall always-on, grounding lobes on grounded paths and absent on social
turns, the reply lobe always), read via the no-LLM ``PreactAgent.inspect``. Ported from the monorepo
attentionbench onto the agent-sdk public surface; leaf-pure. **FREE / deterministic.** See ``METHOD.md``.

    python benchmarks/attentionbench/run.py            # the deterministic attention modes
    python benchmarks/attentionbench/run.py --report   # + results/attentionbench.html
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from agent_sdk import Harness, PreactAgent, Scenario  # noqa: E402
from agent_sdk.clients import FakeClient  # noqa: E402
from agent_sdk.network.context_builder import DEFAULT_NODE_WEIGHTS, score_relevance  # noqa: E402
from agent_sdk.viewer import write_viewer  # noqa: E402
from benchmarks._shared import compose_verdict  # noqa: E402

RESULTS = HERE / "results"

# recall lobes fire every turn; grounding lobes fire only on grounded paths.
RECALL = {"memory_recall", "session_recall", "ctxvar_resolve"}
SCN = [
    {"id": "relational", "q": "hello there!", "want": {"synthesize", "respond"},
     "absent": {"cite", "filter"}},
    {"id": "qna", "q": "what is the capital of France?",
     "want": {"classify", "synthesize", "cite", "filter", "respond"}, "absent": set()},
    {"id": "research", "q": "compare React and Vue in depth and cite sources",
     "want": {"synthesize", "cite", "filter", "respond"}, "absent": set()},
]


def _ck(cid, ok, detail):
    return {"id": cid, "ok": bool(ok), "detail": detail}


def _payload(checks, metrics=None):
    return {"checks": checks, "n": len(checks), "pass": sum(c["ok"] for c in checks),
            "all_pass": all(c["ok"] for c in checks) and bool(checks), "metrics": metrics or {}}


def _agent():
    return PreactAgent(client=FakeClient(["ok"]), instructions="You are a research assistant.")


def _active(agent, q) -> set[str]:
    return {lb["id"] for lb in agent.inspect(q).lobes if lb.get("activated")}


# ── node attention: relevant outranks flooders; traps drop below the floor ──────────────────────
def run_select() -> dict:
    query = "refund policy for enterprise customers"
    relevant = "Enterprise refund policy: full refund within 14 days for enterprise customers."
    flooders = [
        "The weather in Paris is mild this week.",
        "Our office hours are nine to five.",
        "Cats are small domesticated mammals.",
        "The meeting room is on the third floor.",
        "Quarterly revenue grew in the spring.",
    ]
    floor = 0.05
    rel = score_relevance(query, None, relevant, weights=DEFAULT_NODE_WEIGHTS)["activation"]
    flood = [score_relevance(query, None, f, weights=DEFAULT_NODE_WEIGHTS)["activation"] for f in flooders]
    top_flood = max(flood)
    checks = [
        _ck("select.relevant_outranks", rel > top_flood, f"relevant={rel:.3f} > top_flood={top_flood:.3f}"),
        _ck("select.relevant_above_floor", rel >= floor, f"relevant={rel:.3f} ≥ {floor}"),
        _ck("select.flooders_below_floor", all(f < floor for f in flood),
            f"max flooder={top_flood:.3f} < {floor}"),
    ]
    return _payload(checks, {"relevant": round(rel, 3), "top_flood": round(top_flood, 3)})


# ── lobe activation: recall always-on, grounding on grounded paths, reply always ────────────────
def run_recall(agent) -> dict:
    checks = []
    for s in SCN:
        act = _active(agent, s["q"])
        checks.append(_ck(f"recall.{s['id']}", act >= RECALL, f"recall⊆active? missing={sorted(RECALL - act)}"))
    return _payload(checks)


def run_grounding(agent) -> dict:
    checks = []
    for s in SCN:
        act = _active(agent, s["q"])
        want_ok = s["want"] <= act
        absent_ok = not (s["absent"] & act)
        checks.append(_ck(f"grounding.{s['id']}", want_ok and absent_ok,
                          f"want⊆active={want_ok} absent_clear={absent_ok} active={sorted(act)}"))
    return _payload(checks)


def run_reply(agent) -> dict:
    checks = [
        _ck("reply.respond_always", all("respond" in _active(agent, s["q"]) for s in SCN),
            "the reply lobe fires every turn"),
        _ck("reply.classify_on_qna", "classify" in _active(agent, "what is the capital of France?"),
            "classify lights on the qna router"),
    ]
    return _payload(checks)


def run_determinism(agent) -> dict:
    checks = [_ck(f"determinism.{s['id']}", _active(agent, s["q"]) == _active(agent, s["q"]),
                  "stable activation across two inspects") for s in SCN]
    return _payload(checks)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--label", default="base")
    ap.add_argument("--live", action="store_true")          # accepted for ladder uniformity
    ap.add_argument("--model", default=None)
    ap.add_argument("--trials", type=int, default=1)
    args = ap.parse_args()

    agent = _agent()
    report = await Harness(agent).run(
        [Scenario(input=s["q"], expect_lobes=sorted(s["want"] | RECALL)) for s in SCN])
    payloads = {
        "select": run_select(),
        "recall": run_recall(agent),
        "grounding": run_grounding(agent),
        "reply": run_reply(agent),
        "determinism": run_determinism(agent),
    }
    payloads["grounding"]["metrics"]["lobe_recall"] = report.summary()["lobe_recall"]
    verdict = compose_verdict(payloads, record={"grounding": ["lobe_recall"], "select": ["relevant"]})

    print("── attentionbench ─────────────────────────────────────────────")
    total = ok = 0
    for p in payloads.values():
        for c in p["checks"]:
            print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['id']:<28} {c['detail'][:46]}")
        total += p["n"]
        ok += p["pass"]
    print(f"\nattentionbench: {ok}/{total} checks pass · verdict {verdict['status']} "
          f"· lobe_recall={report.summary()['lobe_recall']}")
    if verdict["reasons"]:
        print("reasons:", "; ".join(verdict["reasons"]))

    if args.report:
        RESULTS.mkdir(exist_ok=True)
        write_viewer(RESULTS / "attentionbench.html", [], label="attentionbench · context axis (OY)",
                     verdict=verdict, modes=payloads)
        print(f"report: {RESULTS / 'attentionbench.html'}")
    return 0 if verdict["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
