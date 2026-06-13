---
name: preact-bench
description: Run the PreAct agent-sdk benchmarks (the free deterministic gate and/or the live LLM benches) and report a normalized verdict. Use when the user says "run the benchmarks", "run skillbench/taskbench/agentbench/extensionbench/coding-agent-bench", "what's the verdict", "is the SDK ready", "is it green", or asks for the readiness of a bench. This is the shared run+parse primitive the bench-first-dev, optimize-verdict, and production-ready skills build on.
---

# preact-bench — run a bench, normalize the verdict

Run one bench (or the whole ladder) for the `agent-sdk` package and return a **normalized verdict**
the other skills can act on. You run the existing harnesses — you never stub, judge, or weaken them.

Always run from the package dir: `/Users/minh/Documents/mezon-bot-ai/packages/agent-sdk`.

## The two tiers

**Free deterministic gate** (no provider, no network — the CI floor):
```bash
bash benchmarks/ci-free-gates.sh        # = uv run pytest -q (the unit suite + invariant tests)
```
Run this first, always. If it is red, stop and fix it before any live bench — a red unit/invariant
gate means the optimization surface is already broken.

**Live benches** (real provider — the real READY/NOT_READY verdicts). All five take `--live` and
exit `0` only when the verdict is **READY**, `1` otherwise:
```bash
python benchmarks/skillbench/run.py        --live --report                 # skills: parse/map/activation/follow/funnel
python benchmarks/taskbench/run.py         --live                          # long-rail task planning & execution
python benchmarks/agentbench/run.py        --live --report                 # the integrated memory/recall mission
python benchmarks/extensionbench/run.py    --live --report                 # plugins/MCP as plug-and-play surface
python benchmarks/coding-agent-bench/run.py --live                         # full survey→plan→investigate→document
```
Useful flags: `--trials 3` (skillbench, coding-agent-bench — pool variance), `--model <id>`,
`--capability N` / `--task <id>` (taskbench), `--target <dir>` (coding-agent-bench),
`--replay` (coding-agent-bench free tier). When unsure, `python benchmarks/<name>/run.py --help`.

Provider creds load automatically from `.env` (SDK-local first, then repo root) via
`benchmarks/_shared/provider.py`. If a bench prints "only runs live / set a provider token", the
creds did not load — surface that, do not fake it.

## What to report (the normalized verdict)

Every bench composes its verdict through `benchmarks/_shared/verdict.py:compose_verdict`, shape:
`{status: READY|NOT_READY|UNMEASURED, reasons: [...], gates: {mode_all_pass: bool|None}, metrics: {...}}`.
Read the printed scorecard and the verdict line, then emit:

- **status** — READY / NOT_READY / UNMEASURED (UNMEASURED = no LLM evidence ran; never "ready").
- **failing checks, grouped by mode** — the `FAIL` rows from the scorecard (e.g. `follow.bp-refusal-01.any`),
  each with its one-line detail. These are the **gating** checks.
- **diagnostics** — rows shown but non-gating (e.g. disclosure ratio, over-activation). Note them,
  but do **not** treat them as failures; they're often flappy by design.
- **headline metrics** — from the `metrics` block.
- **next-look pointer** — the written HTML report path (`results/…html` / `report.html`) and which
  probe records / scenario ids to open to diagnose each failing check.

Keep the rest in `reference/` and read it on demand:
- `reference/benches.md` — per-bench: what it measures, dataset, modes, exact CLI.
- `reference/verdict-model.md` — READY/NOT_READY/UNMEASURED, gates vs diagnostics, exit codes, provider env.
- `reference/optimization-surfaces.md` — the shared knowledge base: how the SDK is tuned (weights,
  registry rows, plugins, prompt/skill content), the probe/inspection APIs, and the 5 invariants.
  The other skills link here.

## Rules
- Run from the package dir; run the free gate before live benches.
- Report the verdict faithfully — failing checks with their details, diagnostics kept separate.
- Never edit a bench/dataset to make it pass here; that's `bench-first-dev` / `optimize-verdict` work,
  and only via a legitimate surface.
