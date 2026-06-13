# The agent-sdk benchmark standard

Every benchmark in `packages/agent-sdk/benchmarks/` is one evaluable slice of the SDK with **a
method (the optimization approach it drives), metrics + gates, and a place in the feedback loop**.
This is the contract a conforming bench satisfies so the `improve-loop` workflow and the skills can
operate any of them uniformly. Scaffold a new one with the **`bench-scaffold`** skill (copies
`benchmarks/_template/`).

## Anatomy (the standard module shape)

```
benchmarks/<name>/
├── README.md          # role: the ONE capability/layer this bench certifies
├── METHOD.md          # the optimization approach + metrics + gates (see below) — REQUIRED
├── run.py             # live runner: --live, --report, --label, --trials; emits a verdict, exits 0 iff READY
├── dataset/           # *.jsonl scenarios, one per line, each with an `expect` contract
├── results/           # <mode>-<label>.{json,html} run outputs (gitignored)
├── verdicts/          # <label>/summary.json — composed verdict snapshots
└── improve/           # the ratchet: best.json + wave-NNN/ + journal.md + history.jsonl (auto-managed)
```

## The three things every bench declares

1. **Method / optimization approach** (`METHOD.md`) — *what SDK surface does failing this bench tell
   you to tune?* Name the lever from `../.claude/skills/preact-bench/reference/optimization-surfaces.md`
   (weight dict → registry row → plugin → prompt/skill content → runtime seam) and the hypothesis
   space. This is what makes the bench *improvable*, not just a pass/fail.
2. **Metrics + gates** — the measured quantities and their thresholds. Gating metrics decide the
   verdict; flappy ones are **diagnostics** (recorded, never gating). State each as
   `metric <op> threshold` (e.g. `precision >= 0.8`, `disclosure_ratio` = diagnostic).
3. **Tier** — free (deterministic, no provider) and/or live (real provider). Live tiers pool variance
   with `--trials N`; never stub the provider (a stubbed bench is a `tests/` integration test).

## The verdict contract

`run.py` composes its verdict via `benchmarks/_shared/verdict.py:compose_verdict` →
`{status: READY|NOT_READY|UNMEASURED, reasons, gates: {mode_all_pass: bool|None}, metrics}` and
exits `0` iff READY. Print a final `… : X/Y checks pass · verdict <STATUS>` line — the loop's
deterministic parser (`improve.py:verdict_from_log`) reads exactly that plus an optional `EXIT=<code>`.

- **READY** — every required mode measured and all gating checks pass.
- **NOT_READY** — a gating check failed (the thing to fix via the method's lever).
- **UNMEASURED** — a required mode produced no evidence; never treat as a pass.

## The feedback loop (how a bench gets better over time)

The ratchet lives in `improve/` and is driven by `benchmarks/_shared/improve_cli.py` (deterministic —
the model diagnoses and implements; the CLI decides keep/revert and records):

- **`best.json`** — the moving baseline (status + passing-gate count) each wave must beat.
- **`wave-NNN/`** — append-only per-wave record: `diagnosis.md` → `rfc.md` → `diff.patch` →
  `before.json`/`after.json` → `decision.json`.
- **`journal.md`** (one line per wave) + **`history.jsonl`** (longitudinal trend).
- **`releases/` + `SOTA.json`** — frozen champion snapshots once a bench holds READY.

The loop: **bench → diagnose the worst gating check → implement the smallest legitimate fix via the
method's lever → re-bench → `delta-gate` keeps it only if it ratcheted up → commit**. When a bench is
green, **`bench-harden`** adds discriminating cases to expose the next gap. The **`improve-loop`**
workflow (`.claude/workflows/improve-loop.js`) runs this autonomously over N waves.

## Invariants a bench change must never break
Leaf isolation · default-network parity · citations-mandatory · core determinism · benches-live-only.
After any change: `uv run python -m pytest -q` + `ruff check/format` (see the optimization-surfaces
reference). A bench may fail a verdict; it may never weaken a gate, stub the provider, or branch the
interpreter to manufacture a pass.
