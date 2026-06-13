---
name: improve-loop
description: Run the PreAct agent-sdk self-improvement feedback loop — sweep the benchmark ladder, read the verdict trend, then ratchet quality upward by closing every failing verdict (optimize-verdict) and, once green, hardening the benches (bench-harden) to expose the next real gap. Repeats, committing per step, with the trend as the scoreboard. Use when the user says "run the feedback loop", "keep improving the SDK", "bench-analyze-improve over and over", "make it better and better", or wants continuous autonomous hardening. For a single drive-to-green pass, use production-ready instead.
---

# improve-loop — bench → analyze → improve, on repeat

The continuous ratchet that makes the SDK better and better. One iteration measures the whole ladder,
improves the SDK where it's failing, and — when everything is green — raises the bar so the next
iteration finds the next real gap. The **trend** in `benchmarks/loop/history.jsonl` is the scoreboard:
the loop's job is to keep its arrow pointing up.

Composes the other skills: **preact-bench** (run/parse), **optimize-verdict** (raise SDK quality),
**bench-harden** (raise bench rigor). Shared knowledge: `../preact-bench/reference/`.

## One iteration

1. **Sweep + read the trend.**
   ```bash
   LOOP_TRIALS=3 LOOP_MODEL=<pin-a-model> bash benchmarks/loop/ladder.sh
   ```
   This runs the free gate + every live bench and prints the readiness matrix and the trend
   (appending a record to `history.jsonl`). Pin a model so iterations are comparable.
   - **Free gate red?** Stop and fix it first — a broken unit/invariant gate means the optimization
     surface itself is broken.

2. **Pick the move for this iteration:**
   - **Any NOT_READY / UNMEASURED → raise SDK quality.** Invoke **optimize-verdict** on each failing
     bench (highest-value first: grounding/refusal > routing/activation > efficiency > coverage). It
     closes each gap with the smallest legitimate fix, keeps the five invariants green, and commits
     per fix — with its own hard-stops.
   - **All measured benches READY → raise bench rigor.** Invoke **bench-harden** on the bench with the
     weakest/easiest coverage to add discriminating cases (near-neighbor, refusal, adversarial,
     per-turn). Expect this to flip a bench back to NOT_READY — that is the ratchet working, not a
     regression. Next iteration's optimize step closes the newly-exposed gap.

3. **Re-sweep + confirm the ratchet.** Run the ladder again; compare to the previous record. A good
   iteration shows one of: more benches READY, fewer failing gating checks, more scenarios covered, or
   a tightened bar that still holds. Record the delta.

4. **Repeat.** Each iteration is one or a few attributable commits. Stop per the budget below.

## Running it continuously

- **Self-paced:** invoke this skill again to run the next iteration.
- **Hands-off:** drive it on an interval with the **loop** skill — `/loop improve-loop` (omit the
  interval to let it self-pace between iterations; live benches take minutes, so a long cadence is
  fine). Each firing performs one ratchet iteration and commits its progress.

## Budget & stop conditions (leave the tree green)
- Bounded by an iteration budget (default ~5 per session, or until the user says stop). State it up
  front and report the trend delta when you stop.
- **Stop and report** when both halves stall: the SDK can't be improved further without a runtime
  change that needs human review (e.g. the `_text_of` thinking-block bug in `skillbench/ANALYSIS.md`),
  **and** the benches can't be hardened with a fair case. A faithful "here's the residual gap and why
  it needs review" beats spinning.
- **Hard-stop** (revert in-flight change, stop) on any invariant/parity failure, a net-negative fix,
  or a fix that would require weakening a gate / branching the interpreter / stubbing a bench.

## The non-negotiables (inherited by every step)
- Never weaken a gating check, branch the interpreter, stub a bench, or break leaf isolation /
  default-network parity / citations-mandatory / core determinism / benches-live-only. After every
  change: `uv run python -m pytest -q` + `ruff check/format` (see
  `../preact-bench/reference/optimization-surfaces.md`).
- The ratchet only goes up: don't weaken a bench case you hardened, and don't undo a committed SDK
  fix to make a number look better. Improvement is monotone across the trend.
- Diagnostics are not gates. "Every gating check passes and every required mode measured" is READY;
  nothing else is.
