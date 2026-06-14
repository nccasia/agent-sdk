# corgictionbech · metacognition — benchmark report

> **Verdict: READY** · 33/33 checks · generated n/a

## Metrics

- `pinned.pinned_steps`: 2

## Modes

### monitor — 6/6

- ✓ `monitor.context_tight` — observed
- ✓ `monitor.empty_lobe_slice` — observed
- ✓ `monitor.empty_step_context` — observed
- ✓ `monitor.inactive_lobe_group` — observed
- ✓ `monitor.low_confidence_path` — observed
- ✓ `monitor.step_disabled` — observed

### regulate — 6/6

- ✓ `regulate.healthy_continue` — action=continue (want continue)
- ✓ `regulate.low_conf_review` — action=meta_review (want meta_review)
- ✓ `regulate.tight_adjust` — action=adjust_lobe_slice (want adjust_lobe_slice)
- ✓ `regulate.empty_skip` — action=skip_step (want skip_step)
- ✓ `regulate.empty_step_retry` — action=retry_step (want retry_step)
- ✓ `regulate.precedence_review` — action=meta_review (want meta_review)

### pinned — 2/2

- ✓ `pinned.cite_never_skipped` — action=meta_review (pinned step must escalate, not skip)
- ✓ `pinned.filter_never_skipped` — action=meta_review (pinned step must escalate, not skip)

### channel — 4/4

- ✓ `channel.apply_default_trim` — apply mode applies the default trim action
- ✓ `channel.apply_withholds_skip` — skip_step needs an explicit allowlist (not default)
- ✓ `channel.observe_never_mutates` — observe is the floor — monitors but never mutates
- ✓ `channel.allowlist_widens` — an explicit allowlist enables skip_step

### plugin_surface — 9/9

- ✓ `surface.lobes` — lobes=['meta_context', 'nav_brief']
- ✓ `surface.stage` — stages=['meta_reflect']
- ✓ `surface.flow` — flows=['meta']
- ✓ `surface.tool` — tools=['meta_control']
- ✓ `enact.skills_write` — use_skills writes skills_in_use and strips pinned cite/filter
- ✓ `enact.flow_write` — bias_flow records the next-turn flow bias
- ✓ `enact.navigate_write` — navigate records the phase-cursor request (redo/goto/done)
- ✓ `enact.pinned_never_skipped` — a grounding step (cite/filter) is never a meta skip decision
- ✓ `enact.navigate_never_targets_pinned` — navigate cannot target a pinned grounding step

### plan_compile — 6/6

- ✓ `plan.single_no_fanout` — one aspect → ['act']
- ✓ `plan.expands_act_per_aspect` — three aspects → ['act', 'act', 'act', 'synthesize', 'cite', 'filter']
- ✓ `plan.subjects_threaded` — act subjects = ['cost', 'scale', 'ops']
- ✓ `plan.synthesize_folds` — states=['act', 'act', 'act', 'synthesize', 'cite', 'filter']
- ✓ `plan.pinned_grounding_appended` — grounded tail = ['cite', 'filter']
- ✓ `plan.deterministic` — same plan → same compiled states
