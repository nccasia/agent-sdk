#!/usr/bin/env python3
"""<benchname> — LIVE benchmark skeleton. Copy this folder via the `bench-scaffold` skill.

Implements the agent-sdk benchmark standard (`../_shared/TEMPLATE.md`): a free deterministic mode +
a live mode, composed into a `compose_verdict()` verdict, printing the `X/Y checks pass · verdict
<STATUS>` line the improve-loop parser reads, exiting 0 iff READY. Replace every TODO; keep the
contract.

    python benchmarks/<benchname>/run.py --live --report --label base
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # packages/agent-sdk on path
from benchmarks._shared import compose_verdict, load_provider, write_consolidated  # noqa: E402

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset"
RESULTS = HERE / "results"


def _scenarios() -> list[dict]:
    f = DATASET / "scenarios.jsonl"
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()] if f.exists() else []


def run_free() -> dict:
    """Deterministic tier — no provider. TODO: assert real structure/lint/scoping invariants."""
    checks = []
    for s in _scenarios():
        ok = bool(s.get("id")) and bool(s.get("query")) and "expect" in s
        checks.append({"id": f"dataset.{s.get('id', '?')}.valid", "ok": ok,
                       "detail": "id+query+expect present" if ok else "missing required field"})
    n = len(checks)
    return {"checks": checks, "n": n, "pass": sum(c["ok"] for c in checks),
            "all_pass": all(c["ok"] for c in checks), "metrics": {"scenarios": n}}


async def run_live(model: str) -> dict:
    """Live tier — real provider. TODO: build the agent, probe each scenario, score METHOD.md's gates."""
    from agent_sdk import PreactAgent, probe  # noqa: F401  (probe → ProbeRecord with the full trace)
    from agent_sdk.clients import AnthropicClient

    agent = PreactAgent(client=AnthropicClient(model), instructions="TODO: the bench's system prompt")
    checks = []
    for s in _scenarios():
        rec = await probe(agent, s["query"], label=s["id"])
        # TODO: replace with the real gate from METHOD.md (precision/recall/follow/…).
        ok = rec.status == "ok"
        checks.append({"id": f"live.{s['id']}.answered", "ok": ok, "detail": rec.status})
    n = len(checks)
    return {"checks": checks, "n": n, "pass": sum(c["ok"] for c in checks),
            "all_pass": all(c["ok"] for c in checks) and n > 0, "metrics": {"answered": sum(c["ok"] for c in checks)}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="run the live tier (real provider)")
    ap.add_argument("--report", action="store_true", help="write results/<label>.html")
    ap.add_argument("--label", default="base")
    ap.add_argument("--model", default=None)
    ap.add_argument("--trials", type=int, default=1)  # TODO: pool variance across trials for live gates
    a = ap.parse_args()

    payloads: dict[str, dict | None] = {"free": run_free()}
    if a.live:
        model = a.model or load_provider()
        if not model:
            print("<benchname> live tier needs a provider token — set one in packages/agent-sdk/.env.",
                  file=sys.stderr)
            payloads["live"] = None  # → UNMEASURED
        else:
            import asyncio
            payloads["live"] = asyncio.run(run_live(model))

    verdict = compose_verdict(payloads, record={"free": ["scenarios"], "live": ["answered"]})
    total = sum(p["n"] for p in payloads.values() if p)
    ok = sum(p["pass"] for p in payloads.values() if p)
    for name, p in payloads.items():
        if p:
            for c in p["checks"]:
                print(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['id']:<46} {c['detail'][:54]}")
    if a.report:
        RESULTS.mkdir(exist_ok=True)
        write_consolidated(path=str(RESULTS / f"{a.label}.html"), verdict=verdict, modes=payloads,
                           probes=[], label=f"<benchname> · {a.label}")
        print(f"report: {RESULTS / f'{a.label}.html'}")
    print(f"\n<benchname>: {ok}/{total} checks pass · verdict {verdict['status']}")
    return 0 if verdict["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
