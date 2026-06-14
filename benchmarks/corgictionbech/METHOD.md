# Method — corgictionbech

> The optimization approach for this bench. Standard: `../_shared/TEMPLATE.md`.
> Runnable + deterministic (no provider) — see `README.md`.

## What it certifies
Two tiers:

1. **Deterministic floor (free, no provider).** The kernel `monitor→regulate` decision table
   (precedence, thresholds, boundaries), the apply/observe channel, the pinned-step guards
   (`cite`/`filter` are never skippable), AND the shipped **`MetacognitionPlugin` surface** — the
   `meta_context`+`nav_brief` lobes, the `meta_reflect` stage, the `meta` flow, and the single
   `meta_control` tool whose enactors (`use_skills` / `bias_flow` / `regulate` / `navigate`) write the
   right turn-state keys (reason → write → enact), with `navigate` refusing to target a pinned step.
   (Metacognition reshapes the CURRENT approach; delegation/fan-out is a *separate* plugin, not a meta
   action — so the bench no longer tests `fan_out`.) Surface checks are "contains", resilient to growth.
2. **Live stress test (`--live`).** Run the **equipped** agent on REALLY HARD complex problems —
   reasoning traps, multi-constraint logic, decomposition, false premises (`dataset/scenarios.jsonl`).
   Each has a checkable answer; pool over `--trials`. Headline = the aggregate `solve_rate` (does
   metacognition + the flow actually crack them); `meta_engagement` records how often the agent
   reshaped its thinking, and `by_category` shows which problem kinds it handles. Gated on the
   aggregate solve-rate (LLM variance — a single hard miss isn't a gate).

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
