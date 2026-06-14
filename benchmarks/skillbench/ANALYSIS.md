# skillbench — the good, the bad, the ugly (and what to do about it)

A review of the SDK's skill subsystem through skillbench, written while hardening the
dataset so the bench actually *discriminates*. The prior report was all-green
(`READY`, every skill `READY`, activation P/R all `1.0`) — which told us nothing, because
the scenarios were too easy. This doc records what the bench does well, where its coverage
was thin, the latent runtime risks it never exercised, and concrete fixes. Live numbers
from the hardened run are in the last section.

## The Good — what the design gets right

- **Deterministic scoring, no LLM judge.** Every group is scored from the `probe()` trace by
  pure functions (`scoring.py`). Activation is structural (counting `ActivateSkill` calls),
  follow is substring assertions, funnel reads the obs-tail series. Reproducible and cheap.
- **Free/LLM split.** `lint`/`parse`/`mapping` are pure functions of the fixtures + rendered
  prompt and run even without a model; only `activation`/`follow`/`funnel` call the provider.
- **`UNMEASURED` guard.** `per_skill_verdict` refuses to call a skill `READY` if no LLM group
  produced a row for it — "absence of measurement is not readiness." This is the right
  default and it's what flagged the `billing_policy` gap below.
- **Variance-robust gates.** Activation pools TP/FP/FN across `--trials`; funnel gates on a
  *binary* (`navigated` — did it use `skill.read`/`skill.search`?) and demotes the flappy
  token-ratio to a non-gating diagnostic. Good instinct: gate on what's stable.
- **Real i18n stress.** The accented-slug case (`bảo lưu` → `reservation-of-study-bao-luu`)
  exercises NFC-normalized section matching, a genuine edge.

## The Bad — coverage gaps (now closed)

- **No discriminating power.** Everything was `1.0`/`READY`. A bench that never fails can't
  catch a regression. Root cause: distractors were far apart (code vs weather vs billing)
  and each scenario tested one skill in isolation.
- **The richest fixture was unmeasured.** `billing_policy` ships a precise numeric policy
  (full refund ≤14d / prorated 15–30d / declined >30d, **110%** goodwill credit, 48h→tier-2,
  60-day VIP grace, never refund on an open chargeback) yet had **zero `follow` checks** — so
  it was activation-only and never even appeared in the per-skill verdict. The bench's own
  `UNMEASURED` guard was right; the dataset just hadn't supplied the cases.
- **No near-neighbor confusion.** Only one near pair existed (`code_review`/`ticket_triage`).
  The genuinely confusable triad already in the corpus — `billing_policy` (SaaS refunds) vs
  `course_advisor` (tuition refunds, both citing "14 days") vs `ticket_triage` (billing
  tickets) — was never pitted against itself, so activation *precision* was never under load.
- **A dead multi-turn case.** `cross-skill-01`'s only `expect_activation` target was an
  *eager* skill (skipped in the activation tally), and it had no follow-up turn for the
  `code_review` hand-off it described. Activation was also UNIONed across turns, so it could
  never assert *which* turn activated what.
- **No refusal / anti-hallucination case.** Nothing asked for a figure outside the policy and
  checked that the model escalates instead of inventing one.

**Closed by:** `follow` uplift on six `billing_policy` cases (exact figures + digit traps),
five `near_neighbor` cases over the confusable triad, two `refusal` cases, a per-turn
`skill_switch` case (new `expect_activation_turns` field) replacing the dead one, and an
`over_activation` case.

## The Ugly — latent runtime risks the bench never exercised

These are properties of the *runtime*, surfaced by the Explore pass, that no scenario tested:

1. **Over-activation is unguarded.** `ActivateSkill` is plural — nothing stops the model
   activating three skills when one was right, and `skill_active` then drives all of them.
   The `_ON_DEMAND_DIRECTIVE` says "call ActivateSkill" (singular) but the tooling allows a
   pile-up. **Now measured** by the `activation.overreach_scenarios` / `avg_skills_activated`
   diagnostics; the `near_neighbor`/`over_activation` cases generate the pressure.
2. **The ToC navigability check was broken.** `parse` computed section count as
   `toc.count("\n- ") + 1`, which over-counts by one — a *single-section* large file read as
   "2 sections" and **passed**. So `parse` could never flag a non-navigable dump. Caught by
   the new `_bad_headings` fixture; **fixed** to count `len(split_sections(content))`.
3. **Missing-section recovery was untested.** A `skill.read` for a section that doesn't exist
   returns an `Error:` string; nothing checked whether the model re-navigates or gives up.
   **Now a diagnostic** (`recovery.read_misses_recovered`).
4. **`course_advisor` dumps ~37–41% of its bundle.** The disclosure ratio sat *above* its
   0.35 bar on every catalog/pricing case in the prior run — demoted to diagnostic, so it
   never bit. That's a real "it pulls too much," not just model noise (see suggestions).
5. **Unguarded slug case-sensitivity and no deactivation affordance** — skills stay
   `in_use` across turns with no `DeactivateSkill`, inviting multi-turn state drift. Noted;
   not yet fixed (would be runtime changes, out of scope for a bench PR).

## Suggested improvements (runtime, ranked)

1. **Single-skill-per-turn nudge.** Tighten `_ON_DEMAND_DIRECTIVE` to "activate the *one*
   best-matching skill" and/or have `skill_active` drive at most the top-ranked in-use skill,
   to curb over-activation. Re-measure with `activation.overreach_scenarios`.
2. **Cut `course_advisor`'s disclosure.** Either lower the surface budget or strengthen the
   funnel directive ("read one section, answer, stop") so the disclosure ratio drops under
   0.35 without losing answer quality. Validate via `compare.py` (the surface-mode A/B).
3. **Recovery affordance.** When `skill.read` errors, return the file's ToC inline in the
   error (it partly does for a bare file read) so the model can re-target in one hop; track
   `recovery.read_misses_recovered` → 1.0.
4. **Normalize slugs** at registry build (`policy_skill_slugs`) so a mis-cased policy entry
   still resolves.
5. **A `DeactivateSkill` tool** (or auto-expire `skills_in_use` when a checklist hits its
   terminal step) to bound multi-turn skill state.

## Bench improvements shipped here

- `_bad_headings` + `_bad_checklist` adversarial fixtures; `lint.rejects[...]` now rejects a
  bad skill caught by **any** gate (description, ToC, checklist), not just the description.
- Fixed the `parse` ToC count (`len(split_sections)`); it can now fail a non-navigable file.
- `expect_activation_turns` for per-turn activation; `skill_switch` + `over_activation` +
  `near_neighbor` + `refusal` + `navigation` categories.
- New diagnostics: over-activation and missing-section recovery.

## Live results (hardened run, MiniMax-M2.7, `--trials 3`)

The hardened corpus **flipped the verdict `READY → NOT_READY`** — the bench now discriminates.
Highlights (see `results/skillbench.html`):

- **`billing_policy` is now measured** and follow-scored on its exact figures: prorated
  15–30d refund, **110%** goodwill credit (with a `not[100%]` trap that passed), 48h→tier-2
  escalation, VIP grace, never-refund-on-chargeback — all `follow` checks passed. The
  `UNMEASURED`→measured gap is closed.
- **Near-neighbor pressure exposed real over-firing:** with the confusable triad pitted
  against itself, `billing_policy` activation precision dropped off 1.0 and stayed there
  across both runs — **0.889** (tp24 **fp3**) then **0.923** (tp24 **fp2**): it activated on
  near-neighbor queries it should have declined. `course_advisor`, `ticket_triage`,
  `code_review` held at 1.0. This is the discriminating signal the old corpus lacked, and
  it's reproducible.
- **Over-activation diagnostic:** `activation.overreach_scenarios` = 1/29 and
  `avg_skills_activated` ≈ 0.9 in both runs — the model is generally disciplined (≤1 skill
  per turn), with isolated pile-ups. Worth watching as the corpus grows.
- **Recovery works:** `recovery.read_misses_recovered` ≈ 12/14 then 8/11 — after a
  `skill.read`/`search` error, the model re-navigates ~75–86% of the time rather than giving
  up.
- **Disclosure ratio is genuinely flappy:** the same `course_advisor` cases swung between
  **0.18–0.26** and ~0.36–0.42 across the two runs. Vindicates keeping it a non-gating
  diagnostic and gating on the binary `navigated` instead.

Both `--trials 3` runs agreed on the verdict (`NOT_READY`), the two gating refusal failures,
and the `billing_policy` precision drop — the signal is stable, not a one-off.

### The two gating `follow` failures — a real defect, not a content miss

`ca-refusal-01` (PhD program — not in the catalog) and `bp-refusal-01` (crypto payment — not
in the policy) **failed**, and the trace shows *why*: the model burned all **6 hops** (the
`max_hops` cap) searching for information that does not exist, and on the forced-final hop
(`tools=0`) it emitted a *recovered* `tool_use` (MiniMax `<invoke>` markup) instead of prose
— so **the turn ended with no readable answer**. The captured "answer" was the
`str(ThinkingBlock(...))` repr of an intermediate reasoning step. Two distinct bugs — **both
root-caused and fixed in the SDK**:

1. **Thinking-block repr leak — `engine.py:_block_to_dict`.** Its fallback serialized any
   non-text/non-tool block (a *thinking* block) as `{"type":"text","text": str(block)}` — i.e.
   the Python repr `ThinkingBlock(...)`. That repr then (a) polluted the replayed assistant
   history via `_assistant_content`, so MiniMax **echoed `ThinkingBlock(...)` strings back as
   text** on later hops, and (b) surfaced as the answer through `_text_of`. *Fixed:*
   `_block_to_dict` now emits `{"type":"thinking","text": <reasoning>}` (never a repr) and
   `_assistant_content` **drops thinking blocks from replayed history** so the provider can't
   parrot them. (skillbench also keeps `clean_answer` + the `answer_observable` diagnostic as
   a belt-and-suspenders so any future leak is visible, not silent.)
2. **Forced-final didn't guarantee prose — `engine.py:_agentic`.** On the last (tool-free)
   hop a model that still emits a recovered `tool_use` got no further hop to answer, so the
   turn ended answer-less. *Fixed:* when the loop ends with no text, the engine now runs
   **one more tool-free answer hop** so a reply (a grounded refusal here) is always surfaced.

Both fixes ship with regression tests (`tests/test_engine_robustness.py` A4/A5): a thinking
block is normalized-not-reprd and dropped from history, and an agentic loop that ends on a
tool call is forced to surface prose. Full suite: 324 passing.

**Post-fix live verification.** Re-running the hardened corpus after the two engine fixes,
both refusal cases now pass — `follow.ca-refusal-01.any` ("cannot confirm / not offered") and
`follow.bp-refusal-01.any` ("escalate / manager") — i.e. the model now surfaces a grounded
refusal on out-of-scope queries instead of ending silent, and the answer is real prose, not a
`ThinkingBlock(...)` repr. Verdict returned to **`READY` (100/101)**; the lone non-pass is the
non-gating disclosure-ratio diagnostic. (That verification was a fast `--trials 1` pass —
activation precision/recall there carry low-sample variance, e.g. `billing_policy` P=0.8 at
the floor and one `course_advisor` near-neighbor miss; the authoritative gate is `--trials 3`,
which is what the headline numbers above were measured at.)

**Verdict:** `NOT_READY` — and correctly so. The skill *machinery* (parse / mapping /
activation / navigation / follow on in-scope cases) is solid; the gap is the refusal path on
out-of-scope queries, where the runtime over-explores and fails to surface a grounded
refusal. That is the single highest-value fix the hardened bench points at.
