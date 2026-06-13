#!/usr/bin/env python3
"""coding-agent-bench — the codebase-understanding stress test for agent_sdk.

The flagship task pushes the SDK to its edges: **understand a whole codebase and write its
architecture document.** That exercises every component together — intent routing, a multi-stage
flow (survey → plan → investigate → document), long agentic loops with PreAct, memory-backed
findings aggregation, and file writing.

Three tiers, from free to live:

    python run.py --replay                     # FREE: scripted model on a temp fixture (CI floor)
    python run.py --live                        # LIVE: real provider over the agent_sdk package
    python run.py --live --trials 3             # LIVE × N: aggregate + per-check variance
    python run.py --live --target <dir>         # understand a specific repo
    python run.py --live --update-baseline      # store this run as the regression baseline

Scoring has three faces, all gating: **correctness** (routed / answered / doc written),
**efficiency** (hops / tokens / redundant writes — size-normalized to the repo), and
**accuracy** (the doc's file references must exist; it must cover the real subsystems). A
baseline ratchet additionally fails a run that regresses materially against the last green one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SDK_ROOT = HERE.parents[1]
EXAMPLE = SDK_ROOT / "examples" / "coding-agent"
sys.path.insert(0, str(SDK_ROOT))
sys.path.insert(0, str(EXAMPLE))

from agent_sdk import probe, write_viewer  # noqa: E402
from agent_sdk.clients import make_client  # noqa: E402
from agent_sdk.report import write_json  # noqa: E402
from benchmarks._shared import compose_verdict, load_provider  # noqa: E402

from coding_agent.agent import build_coding_agent  # noqa: E402

UNDERSTAND_TASK = (
    "Explore this codebase and write an architecture document (ARCHITECTURE.md) "
    "introducing the system."
)
BASELINE = HERE / "results" / "baseline.json"
_SKIP = {".git", "__pycache__", ".venv", "venv", "node_modules", "dist", "build"}
_PATH_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]{1,6}")
_CODE_SFX = (".py", ".js", ".ts", ".go", ".rs", ".md", ".toml", ".json", ".yaml", ".yml", ".sh")


# ── accuracy: the doc must be grounded in the real tree ───────────────────────
def _anchors(target: str) -> list[str]:
    """The real subsystems — top-level package dirs + prominent root modules. A
    genuine architecture doc should name most of them. Deterministic per repo."""
    out: list[str] = []
    try:
        for e in sorted(os.listdir(target)):
            full = os.path.join(target, e)
            if e in _SKIP or e.startswith("."):
                continue
            if os.path.isdir(full):
                out.append(e)
            elif e.endswith(".py") and e != "__init__.py":
                out.append(e[:-3])
    except OSError:
        return []
    return out[:14]


def accuracy_metrics(doc_text: str, target: str) -> dict:
    refs = [m for m in dict.fromkeys(_PATH_RE.findall(doc_text)) if m.endswith(_CODE_SFX)]
    missing = [r for r in refs if not os.path.exists(os.path.join(target, r.lstrip("/")))]
    n = len(refs)
    path_ratio = round((n - len(missing)) / n, 3) if n else 1.0
    anchors = _anchors(target)
    low = doc_text.lower()
    present = [a for a in anchors if a.lower() in low]
    cov = round(len(present) / len(anchors), 3) if anchors else 1.0
    return {
        "ref_count": n, "missing_paths": missing, "path_exist_ratio": path_ratio,
        "anchors": anchors, "anchors_present": present, "anchor_coverage": cov,
    }


# ── one run → checks + metrics (pure; reused across trials and tiers) ──────────
def score(rec, target: str, routed: str, *, nfiles: int) -> tuple[list[dict], dict]:
    doc = Path(target) / "ARCHITECTURE.md"
    wrote = doc.exists()
    doc_text = doc.read_text(errors="replace") if wrote else ""

    total_hops = len(rec.llm_calls)
    input_tokens = int(rec.usage.get("input_tokens", 0) or 0)
    writes = [tc for tc in rec.tool_calls if tc.get("name") == "Write"]
    seen: set[str] = set()
    redundant = blocked = 0
    for w in writes:
        if not str(w.get("output", "")).startswith("Wrote "):
            blocked += 1
            continue
        p = (w.get("input") or {}).get("file_path")
        if p in seen:
            redundant += 1
        elif p is not None:
            seen.add(p)

    # Size-normalized budgets: a 30-file and a 300-file repo can't share a ceiling.
    max_hops = max(60, nfiles // 3)
    max_tokens = max(400_000, nfiles * 4_000)
    acc = accuracy_metrics(doc_text, target)
    truncated_final = sum(1 for m in rec.meta_actions if m.get("action") == "truncated_final")

    checks = [
        # correctness
        {"id": "routed", "ok": routed == "understand", "detail": f"path={routed!r}"},
        {"id": "used_tools", "ok": len(rec.tool_calls) > 0, "detail": f"tool_calls={len(rec.tool_calls)}"},
        {"id": "wrote_doc", "ok": wrote,
         "detail": f"{'written' if wrote else 'MISSING'}" + (f" ({len(doc_text.splitlines())} lines)" if wrote else "")},
        {"id": "answered", "ok": rec.status == "answered", "detail": f"status={rec.status!r}"},
        {"id": "not_truncated", "ok": truncated_final == 0, "detail": f"truncated_final={truncated_final}"},
        # efficiency (size-normalized)
        {"id": "hops_bounded", "ok": total_hops <= max_hops, "detail": f"hops={total_hops} (≤{max_hops})"},
        {"id": "tokens_bounded", "ok": input_tokens <= max_tokens, "detail": f"in_tok={input_tokens} (≤{max_tokens})"},
        {"id": "no_redundant_writes", "ok": redundant == 0,
         "detail": f"redundant={redundant} (exec={len(writes) - blocked}→{len(seen)} distinct, blocked={blocked})"},
        # accuracy
        {"id": "paths_grounded", "ok": acc["path_exist_ratio"] >= 0.85,
         "detail": f"exist_ratio={acc['path_exist_ratio']} of {acc['ref_count']} refs"
                   + (f", missing={acc['missing_paths'][:6]}" if acc["missing_paths"] else "")},
        {"id": "covers_subsystems", "ok": acc["anchor_coverage"] >= 0.6,
         "detail": f"anchor_cov={acc['anchor_coverage']} ({len(acc['anchors_present'])}/{len(acc['anchors'])})"},
    ]
    metrics = {
        "hops": total_hops, "input_tokens": input_tokens,
        "cost": rec.usage.get("estimated_cost"), "redundant_writes": redundant,
        "path_exist_ratio": acc["path_exist_ratio"], "anchor_coverage": acc["anchor_coverage"],
    }
    return checks, metrics


# ── baseline ratchet: don't regress past the last green run ────────────────────
def ratchet(metrics: dict) -> dict | None:
    if not BASELINE.exists():
        return None
    base = json.loads(BASELINE.read_text()).get("metrics", {})
    reasons = []
    for k, tol in (("hops", 1.15), ("input_tokens", 1.15)):
        b, c = base.get(k), metrics.get(k)
        if b and c and c > b * tol:
            reasons.append(f"{k} {c} > {round(b * tol)} (baseline {b} +15%)")
    if metrics.get("redundant_writes", 0) > base.get("redundant_writes", 0):
        reasons.append(f"redundant_writes {metrics['redundant_writes']} > baseline {base.get('redundant_writes', 0)}")
    for k in ("path_exist_ratio", "anchor_coverage"):
        b, c = base.get(k), metrics.get(k)
        if b is not None and c is not None and c < b - 0.1:
            reasons.append(f"{k} {c} < baseline {b} −0.1")
    return {"id": "no_regression", "ok": not reasons, "detail": "; ".join(reasons) or "within tolerance vs baseline"}


# ── tier runners ──────────────────────────────────────────────────────────────
async def _run_live(target: str, model: str):
    agent = build_coding_agent(target, client=make_client(model))
    routed = agent.inspect(UNDERSTAND_TASK).path[0]
    rec = await probe(agent, UNDERSTAND_TASK, label=f"live · {model} · {Path(target).name}")
    return rec, routed


async def _run_replay(workdir: str):
    """FREE tier: the scripted understand model over a small temp fixture — exercises
    the whole pipeline (loop, guards, repo map, glob) with no provider call."""
    from coding_agent.fakes import CALCULATOR_PY, TEST_CALCULATOR_PY, make_understand_client

    Path(workdir, "calculator.py").write_text(CALCULATOR_PY)
    Path(workdir, "test_calculator.py").write_text(TEST_CALCULATOR_PY)
    agent = build_coding_agent(workdir, client=make_understand_client())
    routed = agent.inspect(UNDERSTAND_TASK).path[0]
    rec = await probe(agent, UNDERSTAND_TASK, label=f"replay · {Path(workdir).name}")
    return rec, routed


def _aggregate(trials: list[tuple[list[dict], dict]]) -> tuple[list[dict], dict]:
    """Per-check majority pass + per-metric min/mean/max across trials."""
    ids = [c["id"] for c in trials[0][0]]
    agg_checks = []
    for cid in ids:
        rows = [next(c for c in t[0] if c["id"] == cid) for t in trials]
        passes = sum(1 for r in rows if r["ok"])
        agg_checks.append({
            "id": cid, "ok": passes > len(trials) / 2,
            "detail": (rows[-1]["detail"] + (f" · {passes}/{len(trials)} trials pass" if len(trials) > 1 else "")),
        })
    keys = trials[0][1].keys()
    agg_metrics = {}
    for k in keys:
        vals = [t[1][k] for t in trials if isinstance(t[1].get(k), (int, float))]
        agg_metrics[k] = round(sum(vals) / len(vals), 4) if vals else trials[-1][1].get(k)
    return agg_checks, agg_metrics


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="run live (real provider calls)")
    ap.add_argument("--replay", action="store_true", help="FREE scripted tier (no provider)")
    ap.add_argument("--target", default=None, help="repo to understand (default: agent_sdk)")
    ap.add_argument("--trials", type=int, default=1, help="live trials to aggregate (variance)")
    ap.add_argument("--update-baseline", action="store_true", help="store this run as the regression baseline")
    args = ap.parse_args()

    if not (args.live or args.replay):
        print("Pass --replay (free) or --live (real provider calls).", file=sys.stderr)
        return 2

    last_rec = None
    if args.replay:
        with tempfile.TemporaryDirectory() as wd:
            target = wd
            nfiles = 2
            print(f"[coding-agent-bench] replay (free) · target={Path(wd).name} (scripted model)\n")
            rec, routed = await _run_replay(wd)
            last_rec = rec
            trials = [score(rec, target, routed, nfiles=nfiles)]
            # accuracy gates evaluated while the fixture still exists
            agg_checks, agg_metrics = _aggregate(trials)
            _report(agg_checks, agg_metrics, rec, routed, args, baseline_gate=None)
            return 0 if all(c["ok"] for c in agg_checks) else 1

    model = load_provider()
    if model is None:
        print("LIVE bench — set a provider token in packages/agent-sdk/.env "
              "(MINIMAX_API_KEY/MINIMAX_BASE_URL or ANTHROPIC_*).", file=sys.stderr)
        return 2
    target = os.path.abspath(args.target or (SDK_ROOT / "agent_sdk"))
    nfiles = sum(len(f) for _, _, f in os.walk(target))
    print(f"[coding-agent-bench] live · model={model} · target={target} (~{nfiles} files) · trials={args.trials}\n")

    trials = []
    for i in range(max(1, args.trials)):
        rec, routed = await _run_live(target, model)
        last_rec = rec
        trials.append(score(rec, target, routed, nfiles=nfiles))
        if args.trials > 1:
            print(f"  trial {i + 1}/{args.trials}: hops={trials[-1][1]['hops']} "
                  f"cost=${trials[-1][1]['cost']} paths={trials[-1][1]['path_exist_ratio']}")

    agg_checks, agg_metrics = _aggregate(trials)
    base_gate = ratchet(agg_metrics)
    status = _report(agg_checks, agg_metrics, last_rec, routed, args, baseline_gate=base_gate)
    if args.update_baseline:
        BASELINE.write_text(json.dumps({"metrics": agg_metrics}, indent=2))
        print(f"baseline updated → {BASELINE}")
    return 0 if status == "READY" else 1


def _report(checks, metrics, rec, routed, args, *, baseline_gate) -> str:
    gated = list(checks) + ([baseline_gate] if baseline_gate else [])
    payload = {"all_pass": all(c["ok"] for c in gated), "checks": gated}
    verdict = compose_verdict({"understand": payload})
    print(f"probe: routed={routed} stages={[s['stage'] for s in rec.stages]} "
          f"hops={metrics['hops']} in_tok={metrics['input_tokens']} cost=${metrics['cost']} "
          f"paths={metrics['path_exist_ratio']} subsystems={metrics['anchor_coverage']}")
    for c in gated:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['id']:<20} {c['detail']}")
    print(f"\ncoding-agent-bench: {sum(c['ok'] for c in gated)}/{len(gated)} pass · verdict {verdict['status']}")
    write_viewer(HERE / "results" / "coding-agent-bench.html", [rec], label="coding-agent-bench")
    write_json(HERE / "results" / "coding-agent-bench.json", probes=[rec])
    return verdict["status"]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
