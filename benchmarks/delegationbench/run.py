#!/usr/bin/env python3
"""delegationbench — LIVE benchmark: plan-driven fan-out (docs 08/12).

Live-only (no stubs, no fake data). Rich, realistic, multi-faceted queries that a *single*-shot
answer handles worse than a *planned* one. For each scenario it runs the REAL agent
(plan → supervise → execute → fanin) and measures the whole loop:

- **Planning precision/recall** — did the agent write a plan (call ``TodoWrite``) when — and only
  when — the task warranted it? (over-planning guards are the simple/near-neighbor scenarios.)
- **Execution coverage** — on the should-plan cases, did every planned piece get SOLVED? The
  supervisor picks the structure (``blackboard["plan_structure"]``): ``fanout``/``sequential`` run a
  subagent per todo (checked against ``blackboard["todos_results"]``); ``inline`` has the main agent
  work the list in its own stage (checked by a completed answer). All three must solve the pieces.
- **Fan-in fidelity** — did every facet land in the combined final answer?

Each scenario's real probe is written to the report (the rendered plan shows in the Prompt tab).

    python benchmarks/delegationbench/run.py --live --report --label base
    python benchmarks/delegationbench/run.py --live --model claude-opus-4-8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # packages/agent-sdk on path
from agent_sdk import PreactAgent, probe  # noqa: E402
from agent_sdk.plugins.planning import PlanningPlugin  # noqa: E402
from agent_sdk.probe import ProbeRecord  # noqa: E402
from agent_sdk.viewer import write_viewer  # noqa: E402
from benchmarks._shared import compose_verdict, load_provider  # noqa: E402

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset"
RESULTS = HERE / "results"

# Gating thresholds (see METHOD.md).
LIVE_PRECISION, LIVE_RECALL, LIVE_FIDELITY, LIVE_EXEC = 0.80, 0.70, 0.70, 0.70
# Per-scenario wall-clock cap — a stalled provider call is bounded (recorded as an error, not a hang).
SCENARIO_TIMEOUT_S = 180.0


def _scenarios() -> list[dict]:
    f = DATASET / "scenarios.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []


def _pr(should: list[bool], did: list[bool]) -> tuple[float, float]:
    """precision, recall of the plan decision against the labels."""
    tp = sum(1 for s, d in zip(should, did, strict=False) if s and d)
    fp = sum(1 for s, d in zip(should, did, strict=False) if d and not s)
    fn = sum(1 for s, d in zip(should, did, strict=False) if s and not d)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return precision, recall


def _planned(rec: ProbeRecord) -> int:
    """Plan steps the agent wrote (0 ⇒ no plan) — the largest TodoWrite list it sent."""
    sizes = [
        len((tc.get("input") or {}).get("todos") or [])
        for tc in rec.tool_calls
        if tc.get("name") == "TodoWrite"
    ]
    return max(sizes, default=0)


def _subagents(rec: ProbeRecord) -> list[dict]:
    """The per-todo subagent results the engine fanned out (blackboard['todos_results']).
    Skips scratchpad cap markers ({"_elided"}/{"_truncated"}) — only real result rows."""
    return [
        r for r in (rec.blackboard.get("todos_results") or [])
        if isinstance(r, dict) and r.get("status")
    ]


class _NoResearch:
    """Drop the RAG ``research`` flow — this delegation agent has no KB, so a complex query should
    route to the ``plan`` (TodoWrite → fanout) flow, not the general research flow (per METHOD.md)."""

    name = "no_research"

    def install(self, setup) -> None:
        setup.remove_flow("research")


async def run_live(model: str, *, navigator: bool = False) -> tuple[dict, list[ProbeRecord]]:
    from agent_sdk.clients import AnthropicClient

    plugins = [PlanningPlugin(), _NoResearch()]
    meta = None
    if navigator:
        # A/B the Navigator: mount the metacognition faculty + enable its phase moves
        # (redo_phase/goto_phase) so a phase that misses its definition of done is redone.
        from agent_sdk.metacognition_facade import Metacognition
        from agent_sdk.plugins.metacognition import MetacognitionPlugin

        plugins.append(MetacognitionPlugin(flow=False))  # contribute the surface, not a new flow
        meta = Metacognition(
            mode="apply", apply_actions={"adjust_lobe_slice", "redo_phase", "goto_phase"}
        )
    agent = PreactAgent(
        client=AnthropicClient(model),
        instructions=(
            "Answer fully and accurately. When a task has several distinct parts, plan it with the "
            "TodoWrite tool — one todo per part, each with its own prompt and tools — then let each "
            "part run as its own subagent and combine their results into one answer."
        ),
        plugins=plugins,
        **({"metacognition": meta} if meta is not None else {}),
    )
    checks: list[dict] = []
    records: list[ProbeRecord] = []
    should, did, widths, subagent_counts = [], [], [], []
    fidelity_hits, fidelity_total = 0, 0
    exec_hits, exec_total = 0, 0
    scenarios = _scenarios()
    n_scn = len(scenarios)
    for i, s in enumerate(scenarios, start=1):
        exp = s.get("expect", {})
        want = bool(exp.get("delegate"))  # "delegate" label = "this query warrants a plan"
        # Progress to stderr so a long live run is observable (and a stalled scenario is bounded:
        # a per-scenario timeout records an error and moves on instead of hanging the whole run).
        print(f"[{i}/{n_scn}] {s['id']} …", file=sys.stderr, flush=True)
        try:
            rec = await asyncio.wait_for(probe(agent, s["query"], label=s["id"]), SCENARIO_TIMEOUT_S)
        except TimeoutError:
            rec = ProbeRecord(label=s["id"], query=s["query"], status="error",
                              error=f"scenario timed out after {SCENARIO_TIMEOUT_S}s")
        records.append(rec)  # the full live trace per scenario → Inspect / Prompt tabs
        width = _planned(rec)
        planned = width > 0
        print(f"    → {s['id']}: status={rec.status} planned={planned} "
              f"structure={rec.blackboard.get('plan_structure', '-')} "
              f"subagents={len(_subagents(rec))}", file=sys.stderr, flush=True)
        should.append(want)
        did.append(planned)
        subs = _subagents(rec)
        if planned:
            widths.append(width)
            subagent_counts.append(len(subs))
        # Execution coverage: when the agent plans (≥2 steps), every planned piece must be SOLVED —
        # by a subagent (fanout/sequential structures) OR by the main agent itself (inline). Inline
        # is a legitimate structure, not a missing fan-out, so we don't require subagents there; we
        # require the work landed (answered) and let the fidelity check below prove the facets.
        if want and width >= 2:
            exec_total += 1
            structure = rec.blackboard.get("plan_structure") or ("inline" if not subs else "-")
            if structure in ("fanout", "sequential"):
                solved = len(subs) >= max(2, width - 1)  # a subagent ran for (nearly) every todo
            else:  # inline — the main agent worked the list in its own stage
                solved = rec.status == "answered"
            exec_hits += int(solved)
            checks.append(
                {
                    "id": f"live.exec.{s['id']}",
                    "ok": solved,
                    "detail": f"structure={structure} todos={width} subagents={len(subs)}",
                }
            )
        facets = exp.get("answer_contains") or []
        if want and facets:  # fan-in fidelity on should-plan cases with a facet contract
            fidelity_total += 1
            covered = all(f.lower() in (rec.answer or "").lower() for f in facets)
            fidelity_hits += int(covered)
            checks.append(
                {
                    "id": f"live.fanin.{s['id']}",
                    "ok": covered and rec.status == "answered",
                    "detail": f"planned={planned} facets={'all' if covered else 'MISS'}",
                }
            )
        else:
            checks.append(
                {
                    "id": f"live.decision.{s['id']}",
                    "ok": planned == want,
                    "detail": f"want={want} planned={planned} steps={width}",
                }
            )

    precision, recall = _pr(should, did)
    fidelity = fidelity_hits / fidelity_total if fidelity_total else 1.0
    execution = exec_hits / exec_total if exec_total else 1.0
    checks.extend(
        [
            {
                "id": "live.planning.precision",
                "ok": precision >= LIVE_PRECISION,
                "detail": f"{precision:.2f} >= {LIVE_PRECISION}",
            },
            {
                "id": "live.planning.recall",
                "ok": recall >= LIVE_RECALL,
                "detail": f"{recall:.2f} >= {LIVE_RECALL}",
            },
            {
                "id": "live.exec.coverage",
                "ok": execution >= LIVE_EXEC,
                "detail": f"{execution:.2f} >= {LIVE_EXEC} (every planned piece solved: subagent or inline)",
            },
            {
                "id": "live.fanin.fidelity",
                "ok": fidelity >= LIVE_FIDELITY,
                "detail": f"{fidelity:.2f} >= {LIVE_FIDELITY}",
            },
        ]
    )
    n = len(checks)
    payload = {
        "checks": checks,
        "n": n,
        "pass": sum(c["ok"] for c in checks),
        "all_pass": all(c["ok"] for c in checks) and n > 0,
        "metrics": {
            "planning_precision": round(precision, 3),
            "planning_recall": round(recall, 3),
            "execution_coverage": round(execution, 3),
            "fanin_fidelity": round(fidelity, 3),
            "avg_plan_steps": round(sum(widths) / len(widths), 2) if widths else 0,
            "avg_subagents": round(sum(subagent_counts) / len(subagent_counts), 2)
            if subagent_counts
            else 0,
        },
    }
    return payload, records


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--live", action="store_true", help="run the live tier (real provider)")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--label", default="base")
    ap.add_argument("--model", default=None)
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--navigator", action="store_true",
                    help="enable the metacognition Navigator (redo/goto phase moves) — A/B switch")
    a = ap.parse_args()

    payloads: dict[str, dict | None] = {}
    records: list[ProbeRecord] = []
    if a.live:
        model = a.model or load_provider()
        if not model:
            print(
                "delegationbench is live-only — set a provider token in packages/agent-sdk/.env "
                "(or pass --model).",
                file=sys.stderr,
            )
            payloads["live"] = None  # → UNMEASURED
        else:
            payloads["live"], records = asyncio.run(run_live(model, navigator=a.navigator))
    else:
        print(
            "delegationbench is LIVE-ONLY (no fake/free tier). Re-run with --live (needs a "
            "provider token).",
            file=sys.stderr,
        )
        payloads["live"] = None  # → UNMEASURED

    verdict = compose_verdict(
        payloads,
        record={
            "live": [
                "planning_precision",
                "planning_recall",
                "execution_coverage",
                "fanin_fidelity",
                "avg_plan_steps",
                "avg_subagents",
            ]
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
            records,  # real live probes — the Prompt tab shows each turn's rendered plan
            label=f"delegationbench · {a.label}",
            verdict=verdict,
            modes=payloads,
        )
        print(f"report: {RESULTS / f'{a.label}.html'}")
    print(f"\ndelegationbench: {ok}/{total} checks pass · verdict {verdict['status']}")
    return 0 if verdict["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
