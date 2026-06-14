---
name: bench-scaffold
description: Scaffold a new PreAct agent-sdk benchmark from the standard template — a conforming module with a method/optimization approach (METHOD.md), metrics + gates, a live run.py emitting the verdict contract, and the improve/ ratchet wired in. Use when the user says "create a new benchmark", "add a bench for X", "scaffold a benchmark", "we're going to create many benchmarks", or wants a new evaluable slice of the SDK that plugs into the feedback loop.
---

# bench-scaffold — author a new benchmark to the standard

Stamp out a new benchmark that conforms to `benchmarks/_shared/TEMPLATE.md` so it plugs straight into
the loop (`improve-loop` workflow, `optimize-verdict`, `bench-harden`, the `benchmarks/loop` ladder).
The whole point of the standard is that **every** bench declares the same three things — a method, its
metrics/gates, and its place in the ratchet — so one set of skills/workflows drives all of them.

Read `benchmarks/_shared/TEMPLATE.md` (the standard) and
`../preact-bench/reference/optimization-surfaces.md` (the levers) first.

## Steps

1. **Copy the template.** `cp -r benchmarks/_template benchmarks/<name>` (from `packages/agent-sdk`).
2. **Fill `METHOD.md` — this is the heart.** It is what makes the bench *improvable*, not just
   pass/fail:
   - **What it certifies** — the one SDK capability this bench is the arbiter for.
   - **The lever (optimization approach)** — the table mapping each failing dimension → root-cause
     signal in the probe trace → the smallest surface to tune (weight → registry row → plugin →
     prompt/skill content → runtime seam). Name what's in scope and out of scope for a wave.
   - **Metrics & gates** — each metric, its direction, its threshold, and whether it **gates** or is a
     **diagnostic** (flappy signals are diagnostics, never gates).
   - **Tiers** (free / live) and the **READY** bar.
3. **Implement `run.py`.** Replace the `run_free` / `run_live` TODOs with the real checks. Keep the
   contract intact: compose via `_shared/verdict.py:compose_verdict`, print the
   `… : X/Y checks pass · verdict <STATUS>` line, exit `0` iff READY. Build the agent on the public
   surface (`PreactAgent`, `@tool`) and read the trace via `agent_sdk.probe`. Live-only — never stub
   the provider.
4. **Author a discriminating dataset.** `dataset/*.jsonl`, one scenario per line with an `expect`
   contract. Include near-neighbor, refusal/out-of-scope, adversarial, and per-turn cases from the
   start — a bench that can't fail proves nothing (`benchmarks/skillbench/ANALYSIS.md`).
5. **Wire it into the loop.**
   - Add a line to `benchmarks/loop/ladder.sh` (with the right flags — see how the others are wired)
     so the readiness matrix/trend includes it.
   - If it has a free deterministic tier, add it to `benchmarks/ci-free-gates.sh`.
   - The `improve/` ratchet is automatic — `improve_cli.py` manages `best.json`/`wave-NNN`/journal on
     first run.
6. **Red→green the first scenario** with **bench-first-dev**, then verify the free gate + the five
   invariants stay green (`uv run python -m pytest -q`, `ruff check/format`).
7. **Document + commit.** `feat(<name>): new benchmark — <capability>` with a `CHANGELOG.md` line.

## Guardrails
- A new bench must declare its METHOD (lever + metrics + gates) — a pass/fail script with no stated
  optimization approach isn't conforming; it can't be improved by the loop.
- Live-only, no stubs (a stubbed bench is a `tests/` integration test).
- Don't duplicate an existing bench's territory — each certifies one distinct capability/layer.
