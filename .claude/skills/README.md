# agent-sdk skills — the benchmark-driven improvement system

A coherent set of skills (and two `../workflows/` workflows) that run a comprehensive, autonomous
**bench → grow scenarios → diagnose → improve the SDK → re-bench → ratchet** loop. Invoke by name
(`/optimize-bench skillbench`, …). They share one knowledge base under
`preact-bench/reference/`.

## The control flow

```
/optimize-bench <name>     one bench, end to end ── workflow: optimize-bench.js
        │  per round:  baseline (free+live) → GROW (realistic+adversarial) → diagnose
        │              → improve SDK → re-bench → ratchet (keep/revert, commit)
        ▼
/optimize-suite [benches]  the whole suite ──────── workflow: optimize-suite.js
        │              matrix → optimize-bench per bench (priority order) → regression recheck
        ▼
/production-ready          terminal goal: drive everything to READY and ship (delegates to optimize-suite)
```

## Skills

| Skill | Role | Use it to |
|---|---|---|
| **optimize-bench** | control (slash) | `/optimize-bench <name>` — run the full per-bench loop (launches the workflow) |
| **optimize-suite** | control (slash) | `/optimize-suite` — run the loop across the whole suite |
| **production-ready** | control (goal) | drive every bench to READY, shipped |
| **preact-bench** | doer + reference | run a bench / the free gate and report a normalized verdict (the run+parse primitive) |
| **optimize-verdict** | doer | the hands-on single-pass fixer: diagnose a failing check → smallest fix → re-run → commit |
| **bench-harden** | doer | the Grow phase: add realistic + adversarial scenarios to a bench |
| **bench-first-dev** | doer | red→green a specific new behavior (write the scenario first) |
| **bench-scaffold** | doer | author a new benchmark from the template |

## Shared references
- `preact-bench/reference/optimization-surfaces.md` — the tuning surfaces (weights → registry rows →
  plugins → prompt/skill content → runtime) + the five invariants. **Read before changing the SDK.**
- `preact-bench/reference/verdict-model.md` — READY/NOT_READY/UNMEASURED, gates vs diagnostics, exit codes.
- `preact-bench/reference/benches.md` — per-bench: what it measures, modes, CLI.
- `optimize-verdict/reference/verdict-to-surface.md` — failing-check → root cause → smallest surface.
- `optimize-bench/reference/scenario-templates.md` — the Grow-phase playbook: per-bench scenario surfaces
  + schemas + category templates.

## The ratchet
All loops record a deterministic moving-baseline ratchet under each bench's `improve/` (best.json +
append-only `wave-NNN/` + journal + history), managed by `benchmarks/_shared/improve_cli.py`. The model
diagnoses and implements; **the CLI decides keep-or-revert** (status rank + passing-gate count). Dataset
growth re-baselines the bench (new failing scenarios are the point, not a regression). See
`benchmarks/README.md`.

## Non-negotiables (every skill enforces)
Never weaken a gating check, branch the interpreter, stub a bench, or break leaf isolation /
default-network parity / citations-mandatory / core determinism / benches-live-only. After every SDK
change: `uv run python -m pytest -q` + `ruff check/format`. Diagnostics are not gates. When stuck, the
loop reverts and reports — a faithful NOT_READY beats a hollow READY.
