---
name: bench-harden
description: Grow a PreAct agent-sdk benchmark with more data scenarios — realistic (representative in-domain) AND adversarial (near-neighbor, refusal, edge, per-turn) cases that expose the next real SDK gap. The "add more data scenarios" doer behind the optimize-bench Grow phase. Use when adding scenarios to a bench, when the user says "grow the bench", "add more scenarios", "make the benchmark harder", "the bench is too easy", "improve the bench coverage", or "raise the bar".
---

# bench-harden — grow the bench with realistic + adversarial scenarios

A bench that never fails proves nothing, and a bench that only tests easy cases isn't realistic. So the
"add more data scenarios" move grows a bench two ways every round: **realistic** cases (representative
in-domain queries it doesn't cover) and **adversarial** cases (near-neighbor, refusal/out-of-scope,
edge, per-turn) that surface the next gap — which the SDK-fix phase then closes, and the ratchet turns
again. This is the Grow phase the `optimize-bench` loop runs; the concrete per-bench scenario surfaces
+ schemas + category templates live in `../optimize-bench/reference/scenario-templates.md`. This is the
**opposite** of weakening a bench to pass: you add fair, real cases the SDK *should* handle and may not.

`benchmarks/skillbench/ANALYSIS.md` is the worked playbook for this (it flipped skillbench
READY→NOT_READY by hardening the dataset, exposing the real refusal-path defect). Read it first when
hardening skillbench.

## What "harder" means (add these kinds of cases)

- **Near-neighbor distractors** — pit genuinely confusable items against each other so *precision* is
  under load (e.g. the `billing_policy` / `course_advisor` / `ticket_triage` triad that dropped
  precision off 1.0). One-skill-in-isolation cases are softballs.
- **Refusal / out-of-scope** — ask for something outside the policy/catalog and assert the agent
  **refuses grounded** rather than inventing or burning the hop budget. This is where real defects hide.
- **Adversarial fixtures** — vague descriptions, non-navigable large files, degenerate checklists
  (the `_bad_*` skillbench fixtures) that the `lint`/`parse` gates must **reject**.
- **Per-turn assertions** — multi-turn cases with `expect_activation_turns` so you assert *which*
  turn did what, not a union across the conversation.
- **Coverage gaps (UNMEASURED → measured)** — the richest fixtures often ship un-asserted (the
  `billing_policy` `follow` gap). Add the cases that exercise their precise content.
- **Broken-check fixes** — a check that can't fail is dead (the ToC `count("\n- ")+1` off-by-one that
  let a single-section dump pass). Fix the scorer and add the fixture that proves it now fails.

Then, only where genuinely justified by the data, tighten a gating threshold — and *demote*, never
gate on, flappy signals (skillbench rightly gates on the binary `navigated` and keeps the swingy
`disclosure_ratio` a diagnostic).

## Procedure

1. Pick the bench + the weakest dimension (from its scorecard / `--report` HTML / `ANALYSIS.md`).
2. Add the discriminating case(s) to the dataset:
   `benchmarks/skillbench/dataset/scenarios.jsonl` (+ a `dataset/skills/<slug>/SKILL.md` fixture),
   `benchmarks/extensionbench/dataset/behaviors.jsonl`, `benchmarks/taskbench/dataset/tasks.jsonl`,
   or `benchmarks/agentbench/dataset/…`.
3. Run the bench via **preact-bench**. A new **NOT_READY** that points at a real gap is the goal —
   that's the bench earning its keep. (If the new case passes first try, good — make the next one harder.)
4. Keep the free gate + invariants green (`uv run python -m pytest -q`; bench/dataset edits must not
   break the unit suite) and `ruff` clean. Benches stay **live-only** — no stubs.
5. Note what you added (a line in the bench's `ANALYSIS.md` or `README.md`, and `CHANGELOG.md`) and
   commit: `test(skillbench): near-neighbor precision + out-of-scope refusal cases`.
6. Hand the exposed gap to **optimize-verdict** to close it.

## Guardrails
- Harder, not unfair: every case must be something a correct SDK *should* satisfy. No impossible or
  contradictory cases just to force a red.
- The ratchet only goes **up**: never weaken/remove a discriminating case you (or `ANALYSIS.md`)
  added to make a verdict green — that's `optimize-verdict`'s job on the SDK, not the bench's.
- A bench going READY→NOT_READY because you hardened it is **success**, recorded as such — not a
  regression to revert.
- Don't gate on a flappy diagnostic; gate on the stable binary and keep the noisy ratio diagnostic.
