#!/usr/bin/env python3
"""taskbench — can the SDK drive a LIVE model to PLAN and SOLVE realistic multi-step tasks?

No mocks, no scripted model: the agent works over a seeded SQLite database (seed.py) with REAL
tools — db.schema / db.query (a wrong query really errors), a todos.* checklist (the artifact), and
submit. The flow/stages are the DRIVER we grade; the final answer is graded against ground truth
computed from each task's reference SQL (grade.py), so nothing is hand-entered.

It is a forcing function: per capability it reads READY / NOT_READY, and the failures point at
concrete SDK improvements (planning quality, state carry across steps, error recovery, completion).

    python run.py --live                 # real provider over all tasks
    python run.py --live --capability 6  # one capability
    python run.py --live --task top3-products
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SDK_ROOT = HERE.parents[1]
sys.path.insert(0, str(SDK_ROOT))
sys.path.insert(0, str(HERE))

from agent_sdk import probe, write_viewer  # noqa: E402
from agent_sdk.clients import make_client  # noqa: E402
from agent_sdk.report import write_json  # noqa: E402
from benchmarks._shared import compose_verdict, load_provider  # noqa: E402

from agent import build_task_agent  # noqa: E402
from grade import grade_answer, ground_truth  # noqa: E402
from report import write_task_report  # noqa: E402
from seed import build_db  # noqa: E402

CAP_NAMES = {1: "decompose", 2: "drive_to_completion", 3: "state_carry", 4: "tool_orchestration",
             5: "predefined_fastpath", 6: "dependency_order", 8: "branching", 10: "error_recovery",
             11: "long_horizon"}
HOP_CEILING = 70


def load_tasks() -> list[dict]:
    return [json.loads(x) for x in (HERE / "dataset" / "tasks.jsonl").read_text().splitlines() if x.strip()]


def score(rec, store, task: dict, facts: list) -> tuple[list[dict], dict]:
    # The final answer is the `deliver` stage's output (rec.answer), graded vs reference SQL.
    answer = rec.answer or ""
    correct, fact_detail = grade_answer(answer, facts)
    n_q = sum(1 for q in store.queries)
    n_err = sum(1 for q in store.queries if "error" in q)
    hops = len(rec.llm_calls)
    trunc = sum(1 for m in rec.meta_actions if m.get("action") == "truncated_final")
    plan_calls = [tc for tc in rec.tool_calls
                  if tc.get("name") == "todos" and (tc.get("input") or {}).get("action") in ("add", "add_many")]

    checks = [
        {"id": "answered", "ok": bool(answer.strip()), "detail": f"answer_len={len(answer)}"},
        {"id": "answer_correct", "ok": correct,
         "detail": "all facts present" if correct
                   else f"missing={[d['fact'] for d in fact_detail if not d['ok']]}"},
        {"id": "bounded", "ok": hops <= HOP_CEILING and trunc == 0,
         "detail": f"hops={hops}≤{HOP_CEILING} truncated={trunc} queries={n_q}"},
        # diagnostics (shown, not gating) — how the plugin drove it
        {"id": "routed_task", "ok": rec.flow == "task",
         "detail": f"flow={rec.flow!r} (want 'task')", "diag": True},
        {"id": "planned", "ok": len(plan_calls) >= 1,
         "detail": f"todos.add calls={len(plan_calls)}", "diag": True},
    ]
    gating = [c for c in checks if not c.get("diag")]
    metrics = {"correct": correct, "hops": hops, "queries": n_q, "query_errors": n_err,
               "cost": rec.usage.get("estimated_cost"), "answered": bool(answer.strip())}
    return checks, gating, metrics


async def run_task(task: dict, model: str):
    conn = build_db()
    agent, store = build_task_agent(make_client(model), conn)
    t0 = time.perf_counter()
    rec = await probe(agent, task["question"], label=f"live · {task['id']}")
    dur = int((time.perf_counter() - t0) * 1000)
    facts = ground_truth(conn, task["answer_sql"])
    return rec, store, facts, dur


def build_matrix(results: list[dict]) -> tuple[dict, dict]:
    by_cap: dict[int, list[dict]] = {}
    for r in results:
        by_cap.setdefault(r["task"]["capability"], []).append(r)
    measured, unmeasured = {}, {}
    for cap, rows in sorted(by_cap.items()):
        label = f"cap{cap}_{CAP_NAMES.get(cap, cap)}"
        checks = [{"id": f"{r['task']['id']}.{c['id']}", "ok": c["ok"]} for r in rows for c in r["gating"]]
        measured[label] = {"all_pass": all(c["ok"] for c in checks), "checks": checks}
    # cross-cutting derived rows
    allr = [r for rows in by_cap.values() for r in rows]
    measured["cap2_drive_to_completion"] = {
        "all_pass": all(r["metrics"]["answered"] for r in allr),
        "checks": [{"id": f"{r['task']['id']}.answered", "ok": r["metrics"]["answered"]} for r in allr]}
    err_rows = [r for r in allr if r["metrics"]["query_errors"] > 0]
    if err_rows:
        measured["cap10_error_recovery"] = {
            "all_pass": all(r["metrics"]["correct"] for r in err_rows),
            "checks": [{"id": f"{r['task']['id']}.recovered", "ok": r["metrics"]["correct"]} for r in err_rows]}
    else:
        unmeasured["cap10_error_recovery"] = "no db.query errored this run — recovery not exercised"
    return measured, unmeasured


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="run live (real provider calls) — required")
    ap.add_argument("--capability", type=int, default=None)
    ap.add_argument("--task", default=None, help="run a single task id")
    args = ap.parse_args()
    if not args.live:
        print("taskbench is a LIVE bench — pass --live (real provider calls, no mocks).", file=sys.stderr)
        return 2
    model = load_provider()
    if model is None:
        print("Set a provider token in packages/agent-sdk/.env (MINIMAX_API_KEY/… or ANTHROPIC_*).",
              file=sys.stderr)
        return 2

    tasks = load_tasks()
    if args.capability:
        tasks = [t for t in tasks if t["capability"] == args.capability]
    if args.task:
        tasks = [t for t in tasks if t["id"] == args.task]
    print(f"[taskbench] live · model={model} · {len(tasks)} realistic tasks (seeded SQLite)\n")

    results = []
    for task in tasks:
        rec, store, facts, dur = await run_task(task, model)
        checks, gating, metrics = score(rec, store, task, facts)
        ok = all(c["ok"] for c in gating)
        ans = (rec.answer or "")[:70].replace("\n", " ")
        print(f"  [{'PASS' if ok else 'FAIL'}] cap{task['capability']:<2} {task['id']:<30} "
              f"correct={metrics['correct']} q={metrics['queries']}(err {metrics['query_errors']}) "
              f"hops={metrics['hops']} {dur}ms")
        if not metrics["correct"]:
            print(f"        truth={facts}  got=\"{ans}\"")
        results.append({"task": task, "rec": rec, "checks": checks, "gating": gating, "metrics": metrics,
                        "answer": rec.answer or "", "queries": store.queries, "tool_calls": rec.tool_calls,
                        "flow": rec.flow, "status": rec.status, "duration_ms": dur,
                        "tok_in": int(rec.usage.get("input_tokens", 0) or 0),
                        "tok_out": int(rec.usage.get("output_tokens", 0) or 0), "facts": facts,
                        "cap_name": CAP_NAMES.get(task["capability"], str(task["capability"]))})

    measured, unmeasured = build_matrix(results)
    verdict = compose_verdict(measured)
    ready = sum(1 for p in measured.values() if p["all_pass"])
    n_correct = sum(1 for r in results if r["metrics"]["correct"])
    print(f"\n── capability matrix ──  ({n_correct}/{len(results)} tasks solved correctly)")
    for label, p in sorted(measured.items()):
        print(f"  {'READY    ' if p['all_pass'] else 'NOT_READY'}  {label}")
    for label, reason in sorted(unmeasured.items()):
        print(f"  UNMEASURED  {label} — {reason}")
    print(f"\ntaskbench: {ready}/{len(measured)} capabilities READY · verdict {verdict['status']}")

    label = f"live · {model} · {len(tasks)} tasks · {n_correct}/{len(results)} solved"
    out = write_task_report(HERE / "results" / "taskbench.html", results=results, measured=measured,
                            unmeasured=unmeasured, verdict=verdict["status"], label=label)
    write_viewer(HERE / "results" / "taskbench-traces.html", [r["rec"] for r in results], label="taskbench · traces")
    write_json(HERE / "results" / "taskbench.json", probes=[r["rec"] for r in results])
    print(f"report: {out}")
    return 0 if verdict["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
