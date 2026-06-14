#!/usr/bin/env python3
"""flowbench — the bench for the SDK's FLOW axis (OX, progressive execution).

Certifies that each intent resolves to the right named flow and step sequence, that grounded paths
carry the cite/filter contract, that routing is deterministic, and that an unrecognized turn degrades
to a graceful fallback. Ported from the monorepo flowbench onto the agent-sdk public surface;
leaf-pure. **FREE / deterministic** — flow resolution is a pure function of (spec, context), read via
the no-LLM ``PreactAgent.inspect``. See ``METHOD.md``.

    python benchmarks/flowbench/run.py            # the deterministic flow-axis modes
    python benchmarks/flowbench/run.py --report   # + results/flowbench.html
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
from agent_sdk.viewer import write_viewer  # noqa: E402
from benchmarks._shared import compose_verdict  # noqa: E402

RESULTS = HERE / "results"

# The default network's deterministic routing (discovered via inspect; the OX contract under test).
SCN = [
    {"id": "relational-hi", "q": "hello there!", "path": "relational", "flow": ["relational:synthesize"]},
    {"id": "relational-thanks", "q": "thanks, that's great", "path": "relational",
     "flow": ["relational:synthesize"]},
    {"id": "qna-fact", "q": "what is the capital of France?", "path": "qna", "flow": ["qna:synthesize"]},
    {"id": "research-compare", "q": "compare React and Vue in depth and cite sources", "path": "research",
     "flow": ["research:plan", "research:research", "research:synthesize", "research:cite", "research:filter"]},
    {"id": "research-tradeoffs", "q": "research the tradeoffs of microservices vs monolith",
     "path": "research",
     "flow": ["research:plan", "research:research", "research:synthesize", "research:cite", "research:filter"]},
]


def _ck(cid, ok, detail):
    return {"id": cid, "ok": bool(ok), "detail": detail}


def _payload(checks, metrics=None):
    return {"checks": checks, "n": len(checks), "pass": sum(c["ok"] for c in checks),
            "all_pass": all(c["ok"] for c in checks) and bool(checks), "metrics": metrics or {}}


def _agent():
    return PreactAgent(client=FakeClient(["ok"]), instructions="You are a research assistant.")


def run_sequence(agent) -> dict:
    checks = []
    for s in SCN:
        snap = agent.inspect(s["q"])
        ok = snap.path[0] == s["path"] and list(snap.flow) == s["flow"]
        checks.append(_ck(f"sequence.{s['id']}", ok, f"path={snap.path[0]} flow={list(snap.flow)}"))
    return _payload(checks)


def run_grounded(agent) -> dict:
    """The research flow carries explicit cite→filter grounding stages; relational does not ground."""
    research = list(agent.inspect("research the tradeoffs of microservices vs monolith").flow)
    relational = list(agent.inspect("hello there!").flow)
    checks = [
        _ck("grounded.research_cites", research[-2:] == ["research:cite", "research:filter"],
            f"tail={research[-2:]}"),
        _ck("grounded.relational_ungrounded",
            not any(s.endswith(":cite") or s.endswith(":filter") for s in relational),
            f"relational flow={relational}"),
    ]
    return _payload(checks)


def run_determinism(agent) -> dict:
    checks = []
    for s in SCN[:3]:
        a, b = agent.inspect(s["q"]), agent.inspect(s["q"])
        ok = tuple(a.path) == tuple(b.path) and list(a.flow) == list(b.flow)
        checks.append(_ck(f"determinism.{s['id']}", ok, "identical across two inspects"))
    return _payload(checks)


def run_fallback(agent) -> dict:
    snap = agent.inspect("xyzzy plugh frobnicate the borogoves")
    checks = [
        _ck("fallback.graceful", bool(snap.flow), f"unrecognized → flow={list(snap.flow)}"),
        _ck("fallback.terminal_synthesize", any(s.endswith(":synthesize") for s in snap.flow),
            "fallback still reaches a synthesize stage"),
    ]
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
    report = await Harness(agent).run([Scenario(input=s["q"], expect_path=s["path"],
                                                expect_flow=s["flow"]) for s in SCN])
    payloads = {
        "sequence": run_sequence(agent),
        "grounded": run_grounded(agent),
        "determinism": run_determinism(agent),
        "fallback": run_fallback(agent),
    }
    # surface the Harness path-accuracy metric (non-gating) on the sequence payload
    payloads["sequence"]["metrics"]["path_accuracy"] = report.summary()["path_accuracy"]
    verdict = compose_verdict(payloads, record={"sequence": ["path_accuracy"]})

    print("── flowbench ──────────────────────────────────────────────────")
    total = ok = 0
    for p in payloads.values():
        for c in p["checks"]:
            print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['id']:<30} {c['detail'][:44]}")
        total += p["n"]
        ok += p["pass"]
    print(f"\nflowbench: {ok}/{total} checks pass · verdict {verdict['status']} "
          f"· path_accuracy={report.summary()['path_accuracy']}")
    if verdict["reasons"]:
        print("reasons:", "; ".join(verdict["reasons"]))

    if args.report:
        RESULTS.mkdir(exist_ok=True)
        write_viewer(RESULTS / "flowbench.html", [], label="flowbench · flow axis (OX)",
                     verdict=verdict, modes=payloads)
        print(f"report: {RESULTS / 'flowbench.html'}")
    return 0 if verdict["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
