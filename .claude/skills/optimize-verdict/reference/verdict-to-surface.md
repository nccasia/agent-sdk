# Verdict → surface — mapping failing checks to the minimal fix

For each **gating** failing check, name the root cause from the probe trace, then pick the smallest
surface. Surfaces are defined in `../../preact-bench/reference/optimization-surfaces.md`. Always
prefer the lowest row in this table that fixes it; escalate only if it provably can't. Diagnostics
(disclosure ratio, over-activation counts, recovery rate) are **not** in this table — don't optimize
against them.

| Failing check class (example ids) | What the trace shows | Root cause | Smallest surface to try |
|---|---|---|---|
| **Activation precision low** (`activation.<slug>`, e.g. `billing_policy` precision 0.889, tp24 fp3) | The skill fires on near-neighbor queries it should decline | Over-firing: prior too high / signal weight too broad / directive too permissive | Lower `prior_<lobe>` or the skill's signal weight in `weights.py`; tighten the on-demand directive ("activate the *one* best-matching skill"). Re-check precision. |
| **Activation recall low** (`activation.<slug>`) | The skill fails to fire on a clear trigger | Under-firing: prior/signal too low, or a missing path bias | Raise `prior_<lobe>` / the relevant `w_<signal>` / `path_<path>__<member>`; or a registry edge from the triggering lobe. |
| **Over-activation** (gating only if it fails a precision gate; the count is a diagnostic) | Multiple skills activated when one was right | `ActivateSkill` is plural; nothing caps it | Prompt: single-skill-per-turn nudge in `_ON_DEMAND_DIRECTIVE`; or have `skill_active` drive only the top-ranked in-use skill. |
| **Empty lobe slice / empty step context** (`empty_lobe_slice`, `empty_step_context`) | A flow step consulted no lobes / produced no nodes | Missing edge or the step's lobe didn't activate | Add a registry edge (`edge_<src>__<dst>`) or raise the step lobe's prior; or, if the step is genuinely dead, `suggest_axis_optimizations` proposes disabling it (per-bot weight, not core). |
| **Low-confidence / emergent path** (`low_confidence_path`) | Path recognized at <0.55 or emergent | Routing signal too weak / ambiguous between paths | Adjust the deciding `w_<signal>` (`w_route_complex`, `w_simple_shape`, `w_short_query`, `w_anaphora`) or `path_<path>__<member>` bias; verify with a no-LLM `Harness` routing check first. |
| **Context tight / budget overrun** (`context_tight`, funnel peak over cap) | The window is under pressure; obs tail too large | Layer/lobe budget too generous; funnel not trimming | Lower `budget_memory|skill|cognition` or `budget_<lobe>`; strengthen the funnel directive ("read one section, answer, stop"). Metacognition's `adjust_lobe_slice` trims optional lobes at runtime — confirm it's allowed. |
| **Follow content miss** (`follow.<id>.has[...]` / `.any`) — answer lacks a required mandate | The skill's SOP doesn't state the mandated content, or the model didn't reach it | Skill content: add/clarify the mandate in `dataset/skills/<slug>/SKILL.md` (it's a genuine content gap, not tuning). |
| **Funnel dump** (`funnel.<id>.navigated` = false) | Model dumped the whole bundle instead of `skill.read`/`skill.search` | Surface too eager / directive weak / disclosure budget too high | Strengthen the funnel directive; lower the skill surface budget; validate the token trade with `benchmarks/skillbench/compare.py`. (Gate on `navigated`; ignore the flappy `disclosure_ratio`.) |
| **Refusal / out-of-scope failure** (`follow.<id>` on a `refusal` case, e.g. `ca-refusal-01`, `bp-refusal-01`) | Model burns all hops searching for nonexistent info; final hop emits a `tool_use`/thinking-repr, **no readable answer** | Two real runtime bugs (see `skillbench/ANALYSIS.md`): (1) answer-capture `_text_of` surfaces a thinking-block repr; (2) forced-final hop guarantees no prose | **Runtime seam (escalate, with a regression test):** tag/skip thinking blocks in the client / `_text_of`; on a text-less final hop run one tool-free answer-only hop or salvage a grounded refusal; add a "searched, found nothing → refuse now" fast path. High blast radius → full invariant suite + a new `tests/` case proving the behavior. |
| **Plugin not active when plugged / still active when unplugged** (extensionbench `plugin.*` / `unplugged.*`) | A lobe/path/tool/MCP-tool didn't toggle with the plugin | Plugin surface not wired through register/override/enable | Fix in the plugin's folder under `agent_sdk/plugins/<name>/` (its lobes/stages/flows/tools/MCP). Structure is also unit-tested in `tests/test_plugins_full_surface.py`. |
| **Task capability fails** (taskbench `--capability N`) | A todo wasn't decomposed/driven/carried/ordered | Flow/stage wiring or tool allowlist for that capability | Adjust the task flow/stage definition or its tools (data rows); for `UNMEASURED` capabilities the SDK lacks the wiring — that's a feature, do it via `bench-first-dev`, not a hack. |
| **Memory / recall mission miss** (agentbench) | A fact wasn't ingested/superseded/recalled, or bounded-context overran | Recall lobe priors/budgets or scoped-context levers | Tune `w_memory_enabled`, `w_mem_*` scope levers, `prior_memory_recall`, `budget_memory`; verify needle recall didn't regress. |

## Procedure reminders
- **One check, one cause, one change, one re-run.** If the change doesn't flip the named check,
  revert it before trying the next surface — never stack speculative edits.
- **Parity guard:** changing a *default* weight that shifts `test_lobe_network` parity is a
  regression. Bot-specific bias belongs in a per-bot override (`policy.flow_lobe_weights`), not the
  default dict — unless the default genuinely was wrong and the parity fixture is updated as a
  deliberate, documented change.
- **Escalation ladder:** weight → registry row → plugin → prompt/skill content → runtime seam. The
  further down you go, the more tests and justification you owe. Most verdict failures are fixed in
  the first two rows.
- When the trace points at a runtime bug rather than tuning (the refusal row above), that's a
  hard-stop candidate for human review unless the fix is small and fully covered by a new regression
  test plus all five invariants.
