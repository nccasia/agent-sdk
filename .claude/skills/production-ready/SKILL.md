---
name: production-ready
description: Autonomously drive the whole PreAct agent-sdk to production readiness — build a readiness matrix across the benchmark suite, then drive every NOT_READY/UNMEASURED verdict to READY (via the optimize-suite / optimize-bench loop) while keeping the five invariants green, committing per fix. Use when the user says "make the SDK production ready", "get everything to READY", "ship it", "run the full readiness pass", or wants an end-to-end autonomous hardening of the SDK.
---

# production-ready — drive the SDK to all-green, autonomously

The terminal **goal** orchestrator: get every bench to READY and keep it there. Establish the readiness
picture, then close every gap by running the comprehensive **`optimize-suite`** driver (which runs the
per-bench **`optimize-bench`** loop on each bench), enforcing the invariants between every change, and
finishing with a clean summary + history. Fully autonomous, hard-gated: it never weakens a gate,
branches the interpreter, stubs a bench, or breaks an invariant — it stops and reports instead.

Where `optimize-suite` is "make every bench better" (grow + fix, open-ended), `production-ready` is the
**terminal target** "everything READY, shipped." It builds on **preact-bench** (run/parse), the
**optimize-suite**/**optimize-bench** loop, and the shared references under `../preact-bench/reference/`.

## Phase 0 — preconditions
- Confirm a clean-ish working tree (`git status`); note the starting commit so the whole pass is
  bisectable and revertible.
- Confirm provider creds load (a quick `python benchmarks/skillbench/run.py --live` start, or check
  `.env` per `../preact-bench/reference/verdict-model.md`). No creds ⇒ live verdicts are UNMEASURABLE;
  surface that and stop rather than declaring readiness on the free tier alone.
- Run the free gate: `bash benchmarks/ci-free-gates.sh`. Red here blocks everything — fix first.

## Phase 1 — readiness matrix (measure before mutating)
Run all five live benches via preact-bench and tabulate. **Report the matrix before changing
anything** so the user sees the plan:

| Bench | Status | Failing gating checks | Priority |
|---|---|---|---|
| skillbench | … | … | … |
| taskbench | … | … | … |
| agentbench | … | … | … |
| extensionbench | … | … | … |
| coding-agent-bench | … | … | … |

Prioritize: correctness/grounding gaps (refusal, citations, follow) > routing/activation > efficiency
> coverage (UNMEASURED). Resolve UNMEASURED by running the LLM tier — never by lowering the bar.

## Phase 2 — close each gap
For each non-READY bench, in priority order, invoke **optimize-verdict** for that bench. That loop
already enforces: smallest-surface fix → re-run → full suite + invariants + lint green → conventional
commit, with its own hard-stops. Between benches, re-run the free gate + invariants so a fix for one
bench can't silently regress another. Track each commit against the matrix row it closes.

After a bench reaches READY, **re-run the benches already marked READY once** before declaring the
pass done — a later fix must not have regressed an earlier green (cross-bench regression is a stop
condition).

## Phase 3 — finalize
- Update `CHANGELOG.md` (summary of the hardening wave) and `docs/api.md` (any public-surface change).
- Ensure `uv run ruff format agent_sdk` is clean and the full suite is green.
- Produce a closing report: the final readiness matrix (all READY, or the residual hard-stops with
  diagnoses), the commits made (one per fix), and links to the `--report` HTML for each bench.
- Leave the tree green. If anything is still NOT_READY because the only fix is a runtime change that
  needs human review, say so plainly with the trace-backed diagnosis — do not call it ready.

## Guardrails (non-negotiable — same as optimize-verdict, restated because this skill ranges widest)
- Never weaken/skip a gating check, branch the interpreter, or stub a bench to turn a verdict green.
- Never break leaf isolation, default-network parity, citations-mandatory, core determinism, or the
  benches-live-only rule. After every change: `uv run python -m pytest -q` (incl. `test_sdk_isolation`,
  `test_lobe_network`, `test_pinned_lobes_parity`) + `ruff check/format`.
- Diagnostics are not gates; "all gating checks pass + every required mode measured" is the only
  definition of READY (`../preact-bench/reference/verdict-model.md`).
- One fix → one commit. Bounded iterations per verdict. When stuck, stop and report — a faithful
  NOT_READY with a clear next step beats a hollow READY.
- "Production ready" = every live bench READY on the free + live ladder, all five invariants intact,
  lint/format clean, CHANGELOG/docs updated. Anything less is reported as not-yet-ready.
