# skillbench — how good is the SDK's skill system?

**Deterministic fixtures in, the REAL `PreactAgent` driven against a live provider, an
HTML report out.** It measures what the agent actually does with skills — no stubs for the
live groups, no LLM judge (behavior is scored deterministically from the `probe()` trace).

A skill is a **Standard Operating Procedure**: a folder indexed by `SKILL.md` (see
`docs/concepts/09-skills.md`). skillbench asks whether the engine can turn that SOP into
behavior — parse it, map it onto its own stages/tools, activate the right one, follow it,
and keep its content funneled.

## Run (needs a provider token in `packages/agent-sdk/.env`)

```bash
uv --directory packages/agent-sdk run python benchmarks/skillbench/run.py --live --report
# → benchmarks/skillbench/results/skillbench.html  (gitignored)
```

It is **live-only** (agentbench-style): pass `--live`. The `lint` / `parse` / `mapping`
groups are deterministic and run in the same pass; `activation` / `follow` / `funnel` call
the model. `--trials N` runs each scenario N times and pools activation, to average out
live model variance.

## What it measures

| group | cost | what it pushes |
|---|---|---|
| `lint` | free* | adversarial fixtures (slug starts with `_`) are **rejected** — the bench passes when a deliberately-bad skill is flagged by SOME gate: a vague description, a large non-navigable file (no ToC), or a degenerate checklist. |
| `parse` | free* | the SOP folder parses into a usable structure: a non-vague description, a section tree, a **ToC** for a large reference file (not a dump), a working `search_bundle`, a materialized `checklist`. |
| `mapping` | free* | the skill **maps onto the engine**: its block appears only in its declared `stages` and not elsewhere; on-demand → one-line index + the `ActivateSkill` directive **and the tool is exposed**; eager → body inlined. |
| `activation` | LLM | the model **activates the right skill** (calls `ActivateSkill`) on clear/paraphrase triggers and **not** on distractors — per-skill precision / recall (≥ 0.8), pooled over `--trials`. On-demand only (eager skills are always-on, scored by `follow`). |
| `follow` | LLM | the answer **obeys the loaded skill's** mandated behavior (`must_include` / `must_not_include` / `must_include_any` from the scenario's `uplift`). |
| `funnel` | LLM | skill content **navigates, it doesn't dump**: gated on the observation tail staying bounded and the model using `skill.read`/`skill.search` (layered) for a large bundle. The disclosure ratio is reported as a **diagnostic** (it sits near its threshold and flips on model variance, so it doesn't gate). |

\* `lint`/`parse`/`mapping` need no model but execute inside the live invocation. Diagnostic
rows are shown in the report but never gate the verdict. Diagnostics now include:
`activation.overreach_scenarios` / `activation.avg_skills_activated` (over-activation — the
runtime lets `ActivateSkill` fire for several skills at once, with no single-skill-per-turn
guard) and `recovery.read_misses_recovered` (after a `skill.read`/`skill.search` returns an
error, did the model re-navigate or give up?).

**Verdict (per skill):** `READY` / `NOT_READY` / `UNMEASURED`. A skill that no scenario
exercised can pass the free groups but is never `READY` without LLM evidence — *absence of
measurement is not readiness.*

## Fixtures (`dataset/skills/<slug>/SKILL.md`)

Real SOP folders, one per shape the subsystem supports: `code_review` (body-only,
mandated closing line), `course_advisor` (layered bundle with a **large** `catalog.md` →
ToC), `incident_runbook` (eager), `release_checklist` (checklist procedure),
`sprint_tracker` (context_vars), `ticket_triage` (a distractor near `code_review`),
`billing_policy` (a precise numeric policy — exact figures the `follow` cases pin), and
three adversarial fixtures that must be **rejected** by the deterministic gates:
`_bad_vague` (vague description), `_bad_headings` (a large reference file with no navigable
headings → no usable ToC), `_bad_checklist` (checklist steps with neither `title` nor
`ask`). `dataset/scenarios.jsonl` carries the activation / uplift / lifecycle cases.

**Scenario categories:** beyond the clear/paraphrase/distractor/eager/context_vars cases,
the corpus exercises `near_neighbor` (the genuinely confusable triad — `billing_policy`
SaaS refunds vs `course_advisor` tuition refunds vs `ticket_triage` billing tickets, where
exactly one should activate), `refusal` (ask for something not in the policy → escalate /
cannot confirm, never invent a figure), `over_activation` (a multi-domain query that
warrants exactly one skill), `skill_switch` (multi-turn, with per-turn `expect_activation_turns`
asserting *which* turn activates *which* skill), and `navigation` (a deep-`catalog.md`
section read). Scenario fields: `turns`, `expect_activation`, `expect_activation_turns`,
`uplift.{must_include,must_not_include,must_include_any}`, `file_reads.{must_read,must_not_read}`.

## What it found while being built

- **On-demand activation was not wired in the SDK.** The prompt told the model to "call
  `ActivateSkill`", but no such tool was registered and `skills_in_use` was never set — so
  on-demand skills could be *listed* but never *loaded*. skillbench's design forced wiring
  the activation tools (`ActivateSkill` / `skill.read` / `skill.search`) and persisting
  `skills_in_use` on the session. Eager skills already worked (body inlined).
- The `skill_select` / `skill_active` **lobes are not in the SDK's default network** — the
  SDK maps skills to the prompt *directly* (`build_skill_prompt_block`) and the workspace
  state rides the `ActivateSkill` result, not a driving lobe. The `mapping` group measures
  the real direct mapping; the lobe-lifecycle path is a separate, future wiring.
