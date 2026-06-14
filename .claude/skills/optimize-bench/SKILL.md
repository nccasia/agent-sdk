---
name: optimize-bench
description: Trigger the comprehensive per-bench optimize loop on ONE selected benchmark — baseline (free + live LLM) → grow realistic + adversarial scenarios → diagnose → improve the SDK → re-bench → ratchet (keep/revert, commit). Use when the user says "/optimize-bench <name>", "optimize <bench>", "improve skillbench/toolbench/flowbench/…", "run the optimize loop on <bench>", or wants to autonomously drive one benchmark forward. For the whole suite, use optimize-suite.
---

# optimize-bench — optimize one selected benchmark, end to end

The control surface for the comprehensive per-bench loop. You launch the `optimize-bench` **workflow**
(`.claude/workflows/optimize-bench.js`) on the bench the user named, watch it, and report the trend.
Every round makes the **bench richer** (more realistic + adversarial scenarios) and the **SDK better**
(the smallest legitimate fix), on real provider output — until the bench converges.

## Usage

`/optimize-bench <bench> [rounds] [--no-grow] [--no-live] [--model <id>]`

- `<bench>` — one of: `skillbench` `toolbench` `taskbench` `agentbench` `extensionbench`
  `coding-agent-bench` `flowbench` `attentionbench` `corgictionbech`.
- `rounds` — wave budget per run (default 4).
- `--no-grow` — skip the scenario-growth phase (just fix the SDK to the current set).
- `--no-live` — free deterministic tier only (no provider). The three free benches
  (flowbench/attentionbench/corgictionbech) are always free.
- `--model <id>` — pin the provider model for comparable numbers (skillbench/toolbench/agentbench/extensionbench).

Parse the argument, then **launch the workflow**:

```
Workflow({ name: "optimize-bench", args: { bench: "<bench>", rounds: <n>, grow: <bool>, live: <bool>, model: "<id>" } })
```

It runs in the background; watch with `/workflows`. When it finishes, summarize the returned
`{final, journal}` — what scenarios were grown, which checks were fixed, what was kept vs reverted, and
the final verdict.

## What each round does (the loop the workflow runs)

1. **Baseline** — `bash benchmarks/ci-free-gates.sh` (free floor; red → stop), then run the bench's
   **free** tier always + the **live** tier when applicable → a normalized verdict.
2. **Grow** (realistic + adversarial) — author 1–3 realistic + 1–2 adversarial scenarios (near-neighbor /
   refusal / edge / per-turn) into the bench's scenario surface (a `dataset/*.jsonl` line, or the inline
   `SCN` list for the free benches — see `reference/scenario-templates.md`), re-run to see if they
   **bite**, and commit them as their own `test(<bench>): grow scenarios` wave. Growing **re-baselines**
   the bench (new failing scenarios are the point, not a regression).
3. **Diagnose** — pick the worst failing gating check; name the root cause + the smallest lever
   (`../preact-bench/reference/optimization-surfaces.md`, `../optimize-verdict/reference/verdict-to-surface.md`).
4. **Improve** — apply the smallest legitimate SDK fix; keep `pytest` + the five invariants + `ruff`
   green, or revert.
5. **Re-bench + ratchet** — re-run, the deterministic `improve_cli promote` keeps it only if it ratchets
   up (status rank + passing-gate count), commits kept (`fix(<bench>): …`), reverts otherwise.

Stops when the bench is **READY and a grow round finds no new gap** (converged), the round budget is
hit, or a hard-stop (an invariant/parity test fails, or a fix regresses another check) — in which case
it reverts the in-flight change and reports.

## Guardrails (the loop enforces, restated)
- Never weaken a gating check, branch the interpreter, stub a bench, or break leaf isolation /
  default-network parity / citations-mandatory / core determinism / benches-live-only.
- Diagnostics are not gates — don't chase flappy signals. Grow only fair cases a correct SDK *should* pass.
- One change → one re-bench → one commit. The ratchet (not the model) decides keep/revert.

## Relationship to the other skills
- **`optimize-suite`** — run this loop across the whole suite.
- **`bench-harden`** — the scenario-growth doer (the Grow phase reuses it).
- **`optimize-verdict`** — the hands-on single-pass fixer; **`preact-bench`** — run/parse a bench;
  **`bench-first-dev`** — red→green a specific behavior; **`bench-scaffold`** — author a new bench.
  See `../README.md` for the full control-flow map.
