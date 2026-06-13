# Method — attentionbench

> The optimization approach for this bench. Standard: `../_shared/TEMPLATE.md`.
> Runnable + deterministic (no provider) — see `README.md`.

## What it certifies
That the agent attends to the **right context** and the **right lobes/path** fire each turn — node
selection under traps/floods, lobe activation, and path recognition. The OY (context) axis arbiter.

## SDK target (the concept it maps to)
`agent_sdk/network/activation.py` (the activation network + `build_attention`), `agent_sdk/lobes/`
(the lobe registry + weights), `agent_sdk/network/context_builder.py` (`score_relevance`,
node selection). Recognition/activation are **pure functions of (spec, context)** — most modes are free.

## The lever (optimization approach)
| Failing dimension | Root cause | Lever |
|---|---|---|
| attention/selection recall low | relevant node out-scored by traps | node weights (`w_l1`/`w_l2`, thresholds) in `weights.py` / `context_builder` |
| distraction rate high | flood survives the trim | `min_activation` / budget knobs |
| path/lobe misfire | wrong path recognized or lobe dark | `prior_<lobe>`, signal weights, `path_<path>__<member>` bias |

## Metrics & gates (from the monorepo bench)
`attention_accuracy >= 0.8`, `selection_recall >= 0.85`, `distraction_rate <= 0.2`,
`path_accuracy >= 0.85`, `lobe_recall >= 0.85`, `lobe_noise <= 0.2`. Tiers: mostly **free**
(deterministic activation) + a **live** focus/flood slice.
