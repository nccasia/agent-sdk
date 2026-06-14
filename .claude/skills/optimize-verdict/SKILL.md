---
name: optimize-verdict
description: Autonomous continuous-improvement loop for the PreAct agent-sdk — run a live benchmark, read its verdict, and for each failing gating check diagnose the root cause from the probe trace, apply the smallest legitimate fix (weight / registry row / plugin / prompt / skill content), re-run, verify the check flipped with no regression, and commit. Use when the user says "optimize the SDK", "drive skillbench to READY", "fix the failing benchmarks", "improve the verdict", "close the loop on <bench>", or wants the SDK tuned by its own benchmark verdicts.
---

# optimize-verdict — turn each verdict into an improvement

Take one bench (or `all`) from a NOT_READY/UNMEASURED verdict toward READY by **applying** the
smallest legitimate fix per failing gating check, validating, and committing — fully autonomously,
but hard-gated so it can never trade away an invariant or weaken a gate.

**Input:** a bench name (`skillbench` | `taskbench` | `agentbench` | `extensionbench` |
`coding-agent-bench`) or `all`.

Use **preact-bench** to run/parse. Read `../preact-bench/reference/optimization-surfaces.md` (the
surfaces + invariants) and `reference/verdict-to-surface.md` (the failing-check → fix mapping) before
changing anything.

## The loop (per bench)

Run the free gate once up front (`bash benchmarks/ci-free-gates.sh`); if red, fix that first.
Then, for the target bench, repeat up to **N = 4** rounds:

1. **Run + read.** Run the live bench with `--report` via preact-bench. Parse the normalized verdict.
   - `READY` → done; record it and move on.
   - Otherwise collect the **gating** failing checks (ignore diagnostics).
2. **Diagnose** (one check at a time, hardest/highest-value first). Open the `--report` HTML and the
   probe records for the failing scenario: `ProbeRecord` (path/score, lobe activation rows, ReAct
   steps, tool calls, `hints`, `attention`), `inspect_lobe_axis/flow_axis`, and especially
   `suggest_axis_optimizations()` — its proposals are ready-made weight patches. Name the root cause.
3. **Map → minimal fix.** Use `reference/verdict-to-surface.md` to pick the **smallest** surface that
   addresses the named root cause: weight (`weights.py`) → registry row → plugin → prompt/skill
   content → (last resort) runtime seam. Apply exactly one change.
4. **Re-run that bench only.** Confirm the specific gating check flipped **and** the overall verdict
   improved (fewer reasons, or READY). If the check didn't move, revert and try the next-smallest
   surface — don't pile changes.
5. **Regression gate.** Run the full suite + invariants + lint:
   ```bash
   uv run python -m pytest -q
   uv run ruff check agent_sdk && uv run ruff format agent_sdk
   ```
   Must be green — including `test_sdk_isolation`, `test_lobe_network` (parity),
   `test_pinned_lobes_parity`. If any other bench's check regressed, that's a regression too.
6. **Commit.** Conventional, one fix per commit, scoped to the submodule, e.g.
   `fix(skills): decline near-neighbor billing queries (skillbench precision 0.92→1.0)`,
   `perf(context): trim memory budget under pressure`. Add a `CHANGELOG.md` line; update `docs/api.md`
   if public surface changed. Re-loop from step 1.

## Hard-stops (revert the last change, stop, write a diagnosis)

This is gated autonomy. **Stop and report — do not push through — when:**
- the free gate, full suite, or any invariant/parity test goes red and the fix can't keep it green;
- a fix regresses a different gating check (net-negative);
- only **diagnostics** are moving (e.g. chasing the flappy disclosure ratio) — gates are truth;
- **no progress for 2 consecutive rounds**, or N rounds are exhausted;
- the only viable fix would weaken a gate, branch the interpreter, stub a bench, or break an
  invariant — these are never options.

On hard-stop, leave the tree green (revert the in-flight change), and write: the failing check, the
root cause from the trace, the surfaces tried, and the recommended next step (often a runtime fix
that needs human review — like the `_text_of` thinking-block bug in `skillbench/ANALYSIS.md`).

## Notes
- `UNMEASURED` is a first-class target: creds are present, so run the LLM tier to produce evidence
  rather than leaving a skill/capability unmeasured.
- One change → one re-run → one commit. Small, attributable steps keep the loop debuggable and the
  history bisectable.
- Pin `--model` across a session for comparable numbers; use `--trials 3` where variance matters
  (skillbench, coding-agent-bench) so you're optimizing signal, not noise.
