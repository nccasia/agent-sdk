---
name: bench-first-dev
description: Benchmark-first development for the PreAct agent-sdk — express the target behavior as a bench scenario FIRST (red), then implement the minimal change to make it green, keeping the free gate and the five invariants green. Use when adding or changing SDK behavior (a new lobe/flow/plugin/skill, a routing or activation change, a grounding/refusal fix) and the user wants it done test/benchmark-first, or says "add X benchmark-first", "do this red-green", "write the bench first".
---

# bench-first-dev — red → green, the PreAct way

Discipline: **no behavior change lands without a bench scenario that demanded it.** You write the
failing case first, watch it fail, then make the smallest legitimate change to pass it — never by
weakening a gate or branching the interpreter.

Read `../preact-bench/reference/optimization-surfaces.md` before choosing where to implement, and use
the **preact-bench** skill to run and parse verdicts.

## The loop

### 1. Express the target as a scenario (write the test first)
Pick the bench that owns the behavior and add the case to its dataset (no code yet):
- **Skill activation / follow / funnel** → `benchmarks/skillbench/dataset/scenarios.jsonl`
  (and a fixture under `dataset/skills/<slug>/SKILL.md` if it's new). Fields: `id`, `category`,
  `query`, `skills_under_test`, `expect_activation`, optional `expect_activation_turns`, `uplift`
  (`must_include`/`must_not_include`/`must_include_any`), `turns`.
- **Plugin / MCP plug-and-play** → `benchmarks/extensionbench/dataset/behaviors.jsonl`
  (plugged → active; unplugged → gone).
- **Task planning/execution** → `benchmarks/taskbench/dataset/tasks.jsonl` (with a verifiable
  `expected_state`).
- **Memory / recall mission** → `benchmarks/agentbench/dataset/…`.
- **Pure routing/activation** (no LLM needed) → a `Scenario` in a `Harness` (`agent_sdk/bench.py`):
  `Scenario(input=…, expect_path=…, expect_lobes=[…], expect_status=…)` — fastest red→green.

Make the case **discriminating**: a near-neighbor distractor, a refusal/out-of-scope probe, a
per-turn assertion — not a softball. A scenario that can't fail proves nothing (see
`benchmarks/skillbench/ANALYSIS.md` for why the easy corpus told us nothing).

### 2. Confirm red
Run via **preact-bench**. Confirm the new case is **NOT_READY** (gating check fails) or
**UNMEASURED** (no evidence). If it's already green, the scenario isn't testing what you think —
sharpen it. Capture the exact failing check id.

### 3. Implement the minimal change — via a surface, never a branch
From `optimization-surfaces.md`, pick the smallest surface that addresses the failing check:
weight (`weights.py`) → registry row (`LobeRegistry.add_row`) → plugin → prompt/skill content →
(last resort) a runtime seam with a regression test. Make the **one** smallest change.

### 4. Confirm green + no regression
- Re-run the bench via **preact-bench**: the specific check flips and the overall verdict improves.
- Run the free gate + invariants + lint:
  ```bash
  uv run python -m pytest -q
  uv run ruff check agent_sdk && uv run ruff format agent_sdk
  ```
  All green, including `test_sdk_isolation`, `test_lobe_network` (parity), `test_pinned_lobes_parity`.

### 5. Document + commit
- New/changed public API → note it in `docs/api.md`. Always add a `CHANGELOG.md` line.
- Conventional commit, scoped to the agent-sdk submodule:
  `feat(skills): single-skill-per-turn activation nudge` / `fix(engine): …` / `test(skillbench): …`.
  Mention the invariant(s) touched and how you verified them.

## Guardrails
- The scenario comes **before** the implementation. If you implemented first, you skipped the point.
- Don't make the bench pass by editing it to be lenient — only the SDK changes to satisfy a fair case.
- If the only change that passes the case breaks an invariant, stop and report — the scenario or the
  approach is wrong, not the invariant.
