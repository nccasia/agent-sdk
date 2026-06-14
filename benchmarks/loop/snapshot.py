#!/usr/bin/env python3
"""improve-loop snapshot — turn one ladder sweep into a readiness matrix + an append-only trend.

Reads the per-bench logs/exit codes a ``ladder.sh`` run dropped in ``last-run/``, prints the current
readiness matrix, appends one record to ``benchmarks/loop/history.jsonl``, and prints the trend over
the last few iterations so the feedback loop can SEE whether the SDK is ratcheting upward (more
benches READY, fewer failing gating checks) or regressing. Pure stdlib; never calls a provider.

Status precedence per bench: the explicit ``verdict <STATUS>`` line the bench prints wins; else a
missing-credentials message ⇒ UNMEASURED; else the exit code (0 ⇒ READY, non-zero ⇒ NOT_READY).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BENCHES = ["skillbench", "taskbench", "agentbench", "extensionbench", "coding-agent-bench"]
HERE = Path(__file__).resolve().parent
HISTORY = HERE / "history.jsonl"
_VERDICT = re.compile(r"verdict\s+(READY|NOT_READY|UNMEASURED)")
_CHECKS = re.compile(r"(\d+)\s*/\s*(\d+)\s+checks pass")
_NOCREDS = re.compile(r"only runs live|set a provider token|no credentials", re.I)


def _read(p: Path) -> str:
    return p.read_text(errors="replace") if p.exists() else ""


def _status(log: str, exit_path: Path) -> str:
    hits = _VERDICT.findall(log)
    if hits:
        return hits[-1]
    if _NOCREDS.search(log):
        return "UNMEASURED"
    code = _read(exit_path).strip()
    return "READY" if code == "0" else ("NOT_READY" if code else "UNMEASURED")


def _checks(log: str) -> tuple[int, int]:
    m = _CHECKS.findall(log)
    return (int(m[-1][0]), int(m[-1][1])) if m else (0, 0)


def _sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=HERE, text=True
        ).strip()
    except Exception:
        return "?"


def collect(run_dir: Path) -> dict:
    free = "pass" if _read(run_dir / "free-gate.exit").strip() == "0" else "fail"
    benches: dict[str, dict] = {}
    for b in BENCHES:
        log = _read(run_dir / f"{b}.log")
        if not log and not (run_dir / f"{b}.exit").exists():
            benches[b] = {"status": "SKIPPED", "checks_pass": 0, "checks_total": 0}
            continue
        cp, ct = _checks(log)
        benches[b] = {
            "status": _status(log, run_dir / f"{b}.exit"),
            "checks_pass": cp,
            "checks_total": ct,
        }
    measured = [v["status"] for v in benches.values() if v["status"] != "SKIPPED"]
    return {
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha": _sha(),
        "free_gate": free,
        "benches": benches,
        "summary": {
            "ready": measured.count("READY"),
            "not_ready": measured.count("NOT_READY"),
            "unmeasured": measured.count("UNMEASURED"),
            "measured": len(measured),
        },
    }


_GLYPH = {"READY": "✅", "NOT_READY": "❌", "UNMEASURED": "⚠️ ", "SKIPPED": "·  "}


def print_matrix(rec: dict) -> None:
    s = rec["summary"]
    print("── readiness matrix " + "─" * 41)
    print(f"  free gate: {rec['free_gate'].upper()}    @ {rec['sha']}  {rec['time']}")
    for b, v in rec["benches"].items():
        ct = f"{v['checks_pass']}/{v['checks_total']} checks" if v["checks_total"] else ""
        print(f"  {_GLYPH.get(v['status'], '?')} {b:<20} {v['status']:<11} {ct}")
    print(f"  → {s['ready']}/{s['measured']} READY · {s['not_ready']} NOT_READY · {s['unmeasured']} UNMEASURED")


def print_trend(limit: int = 8) -> None:
    if not HISTORY.exists():
        return
    rows = [json.loads(x) for x in HISTORY.read_text().splitlines() if x.strip()][-limit:]
    if len(rows) < 2:
        return
    print("\n── trend (last %d iterations) " % len(rows) + "─" * 31)
    for r in rows:
        s = r["summary"]
        bar = "".join(
            _GLYPH.get(r["benches"].get(b, {}).get("status", "SKIPPED"), "?").strip()[:1] or "·"
            for b in BENCHES
        )
        print(f"  {r['time']}  {r['sha']:<8}  [{bar}]  {s['ready']}/{s['measured']} READY")
    first, last = rows[0]["summary"], rows[-1]["summary"]
    d = last["ready"] - first["ready"]
    arrow = "▲ improving" if d > 0 else ("▼ regressed" if d < 0 else "= flat")
    print(f"  net over window: {arrow} ({d:+d} READY)   (legend: R=✅ N=❌ U=⚠ ·=skipped, order: {', '.join(BENCHES)})")


def main() -> int:
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else (HERE / "last-run")
    rec = collect(run_dir)
    with HISTORY.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print_matrix(rec)
    print_trend()
    # exit non-zero while any measured bench is not READY — a CI/loop continue-signal.
    return 0 if rec["summary"]["not_ready"] == 0 and rec["summary"]["unmeasured"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
