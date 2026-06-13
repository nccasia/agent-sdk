---
name: optimize-suite
description: Run the comprehensive optimize loop across the WHOLE benchmark suite — build a readiness matrix, then drive each bench through the optimize-bench loop (grow scenarios + improve the SDK) in priority order, and re-check for cross-bench regression. Use when the user says "/optimize-suite", "optimize all the benchmarks", "drive the whole suite forward", "improve the SDK across every bench", or wants the full-suite autonomous improvement pass. For one bench, use optimize-bench.
---

# optimize-suite — optimize the whole suite, bench by bench

The full-suite driver. Launch the `optimize-suite` **workflow** (`.claude/workflows/optimize-suite.js`),
which builds a readiness matrix, then runs the per-bench `optimize-bench` loop on each bench in priority
order (grounding/correctness > routing > efficiency > coverage), and re-checks already-green benches for
cross-bench regression at the end.

## Usage

`/optimize-suite [benches] [rounds] [--model <id>]`

- `benches` — optional subset (default: all nine). e.g. `/optimize-suite skillbench toolbench`.
- `rounds` — per-bench wave budget (default 3).

Launch:

```
Workflow({ name: "optimize-suite", args: { benches: [...], rounds: <n>, model: "<id>" } })
```

It runs in the background (watch `/workflows`); the nested per-bench runs appear under it. When done,
report the before/after readiness matrix and flag any regression.

## What it does
1. **Matrix** — free gate + a verdict read of every bench → the readiness picture (reported before any change).
2. **Optimize** — for each bench in priority order, run `optimize-bench` (a READY bench still gets one
   grow/converge round to expose the next gap; a non-READY bench gets the full `rounds`).
3. **Recheck** — re-run every bench; flag anything that regressed (was READY, now not).

Each per-bench loop keeps the five invariants green and commits per kept wave (see `optimize-bench`).
This is the comprehensive, autonomous "make the whole SDK better" pass; `production-ready` is the
terminal "drive everything to READY once" goal that delegates here.
