#!/usr/bin/env python3
"""improve_cli — deterministic ratchet bookkeeping for the improve-loop workflow & skills.

Thin CLI over ``benchmarks/_shared/improve.py`` so the workflow.js driver (and humans) can run the
keep/revert decision and update the moving baseline WITHOUT an LLM in the loop. The model diagnoses
and implements; this CLI decides and records. Run from the ``packages/agent-sdk`` dir.

    # parse a bench run's stdout log → normalized verdict json (status + gates pass/total)
    python benchmarks/_shared/improve_cli.py verdict-from-log <log> [--out before.json]

    # show the moving baseline vs a fresh verdict
    python benchmarks/_shared/improve_cli.py status  <bench_dir> [--verdict after.json]

    # allocate the next append-only wave dir
    python benchmarks/_shared/improve_cli.py wave-new <bench_dir>          # prints "<id> <path>"

    # the ratchet decision: keep this wave's change or revert it?
    python benchmarks/_shared/improve_cli.py delta-gate <bench_dir> --after after.json [--before b.json]

    # commit the wave outcome: write decision.json, journal, history, and (if kept) bump best.json
    python benchmarks/_shared/improve_cli.py promote <bench_dir> --wave <id> --after after.json \
        --label <label> [--model <id>] [--note "what changed"]

    # freeze the current best.json as an immutable release + SOTA pointer
    python benchmarks/_shared/improve_cli.py release <bench_dir> --label <label>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # packages/agent-sdk on path
from benchmarks._shared import improve as I  # noqa: E402


def _load(p: str | None) -> dict | None:
    return json.loads(Path(p).read_text()) if p else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("verdict-from-log"); p.add_argument("log"); p.add_argument("--out")
    p = sub.add_parser("status"); p.add_argument("bench_dir"); p.add_argument("--verdict")
    p = sub.add_parser("wave-new"); p.add_argument("bench_dir")
    p = sub.add_parser("delta-gate"); p.add_argument("bench_dir"); p.add_argument("--after", required=True); p.add_argument("--before")
    p = sub.add_parser("promote"); p.add_argument("bench_dir"); p.add_argument("--wave", required=True)
    p.add_argument("--after", required=True); p.add_argument("--label", required=True)
    p.add_argument("--model", default=""); p.add_argument("--note", default="")
    p = sub.add_parser("release"); p.add_argument("bench_dir"); p.add_argument("--label", required=True)
    a = ap.parse_args()

    if a.cmd == "verdict-from-log":
        v = I.verdict_from_log(Path(a.log).read_text(errors="replace"))
        out = json.dumps(v, indent=2)
        if a.out:
            Path(a.out).write_text(out + "\n")
        print(out)
        return 0

    bench = Path(a.bench_dir)

    if a.cmd == "status":
        best = I.load_best(bench)
        cur = I.verdict_summary(_load(a.verdict)) if a.verdict else None
        print(json.dumps({"best": best, "current": cur}, indent=2))
        return 0

    if a.cmd == "wave-new":
        wid, wd = I.new_wave(bench)
        print(f"{wid} {wd}")
        return 0

    if a.cmd == "delta-gate":
        before = _load(a.before) or I.load_best(bench)
        decision = I.delta_gate(before, _load(a.after))
        print(json.dumps(decision, indent=2))
        return 0 if decision["kept"] else 1

    if a.cmd == "promote":
        after = _load(a.after)
        before = I.load_best(bench)
        decision = I.delta_gate(before, after)
        wave_dir = I.improve_dir(bench) / a.wave
        wave_dir.mkdir(parents=True, exist_ok=True)
        I.write_wave_decision(wave_dir, {"wave": a.wave, "note": a.note, **decision})
        if decision["kept"]:
            I.write_best(bench, after, label=a.label, model=a.model)
        I.append_journal(bench, f"{a.wave}: {'kept' if decision['kept'] else 'reverted'} — {decision['reason']}"
                                + (f" ({a.note})" if a.note else ""))
        I.append_history(bench, {"wave": a.wave, "label": a.label, **decision})
        print(json.dumps(decision, indent=2))
        return 0 if decision["kept"] else 1

    if a.cmd == "release":
        print(json.dumps(I.snapshot_release(bench, label=a.label), indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
