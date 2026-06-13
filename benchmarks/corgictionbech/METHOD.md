# Method — corgictionbech

> The optimization approach for this bench. Standard: `../_shared/TEMPLATE.md`.
> Runnable + deterministic (no provider) — see `README.md`.

## What it certifies
That **metacognition** self-regulates correctly: the monitor→regulate decision table (precedence,
thresholds, boundaries), the apply/observe channel, the surfaced queue, and the pinned-step guards
(`cite`/`filter` are never skippable) — under the production-default config.

## SDK target (the concept it maps to)
`agent_sdk/metacognition/` (`MetaController`, `monitor.py`, `regulator.py`, `model.py`) +
`agent_sdk/inspection.py` (`snapshot_engine`, `inspect_lobe_axis`/`inspect_flow_axis`). Deterministic —
this is a **free** gate (no LLM judges the pipeline).

## The lever (optimization approach)
| Failing dimension | Root cause | Lever |
|---|---|---|
| wrong decision | regulator precedence/threshold off | `agent_sdk/metacognition/regulator.py` decision table |
| pinned step skipped | guard missing | the pinned-step seam (`cite`/`filter` never skippable) — an invariant, never relax |
| apply/observe wrong channel | mode resolution | `MetaController` mode precedence |

## Metrics & gates
Deterministic: every scenario asserts the decision, the apply/observe channel, the surfaced queue,
the narrowed lobe slice, and that pinned steps survive. all-pass gate. **Free** tier.
