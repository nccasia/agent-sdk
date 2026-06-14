# Method — corgictionbech

> The optimization approach for this bench. Standard: `../_shared/TEMPLATE.md`.
> Runnable + deterministic (no provider) — see `README.md`.

## What it certifies
Two tiers:

1. **Deterministic floor (free, no provider).** The kernel `monitor→regulate` decision table
   (precedence, thresholds, boundaries), the apply/observe channel, the pinned-step guards
   (`cite`/`filter` are never skippable), AND the shipped **`MetacognitionPlugin` surface** — it
   assembles its lobe/stages/flow/tool and its tool enactors write the right turn-state keys
   (reason → write → enact). This is what makes the bench *match the implementation*.
2. **Live measurement (`--live`, single-arm).** Run the **equipped** agent (the best configuration:
   `MetacognitionPlugin` + `metacognition="apply"`) on scenarios that name an expected meta lever,
   and check it makes that choice (skills / flow-bias / fan-out) and answers correctly. This is a
   measurement of the best configuration, **not** a with-vs-without A/B.

## SDK target (the concept it maps to)
`agent_sdk/metacognition/` (`MetaController`, `monitor.py`, `regulator.py`, `model.py`),
`agent_sdk/inspection.py` (snapshots), and `agent_sdk/plugins/metacognition/` (the shipped module:
`lobes.py` / `tool.py` / `stages.py` / `path.py`). The floor is deterministic; the live tier reads
real probe traces of the equipped agent.

## The lever (optimization approach)
| Failing dimension | Root cause | Lever |
|---|---|---|
| wrong decision | regulator precedence/threshold off | `agent_sdk/metacognition/regulator.py` decision table |
| pinned step skipped | guard missing | the pinned-step seam (`cite`/`filter` never skippable) — an invariant, never relax |
| apply/observe wrong channel | mode resolution | `MetaController` mode precedence |

## Metrics & gates
- **Floor (free):** every scenario asserts the decision, the apply/observe channel, the narrowed
  lobe slice, that pinned steps survive, and that the plugin surface + enactors are wired. all-pass.
- **Live (`--live`):** per scenario the equipped agent must **answer correctly** (`answer.*`,
  all-pass gate). How often it reaches for the expected meta lever is recorded as
  `decision_hit_rate` (transparency, **non-gating** — forcing a lever on a trivial turn would be
  overreach). Recorded metrics: `accuracy`, `decision_hit_rate`, `meta_tokens_avg`.

The floor always runs (keeps the bench READY in the no-cred ladder); the live tier adds gating
checks when a provider token is present.
