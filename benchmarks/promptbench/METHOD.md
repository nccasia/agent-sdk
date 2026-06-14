# Method — promptbench

> The optimization approach for this bench. Standard: `../_shared/TEMPLATE.md`.
> Free tiers are deterministic (no provider); the judge tier is live — see `README.md`.

## What it certifies
The SDK's prompts are well-**structured** and well-**written**. Three tiers:

1. **structure** (free) — a pure-function property of the composer
   (`agent_sdk/engine.py::_compose_system_segmented`), read off each stage's `system_segments` via a
   `FakeClient` probe: canonical layer order (stable prefix → volatile tail), identity once + first,
   `<env>` last, no section/persona/conversation duplication. No LLM.
2. **quality** (free) — a rule-based lint of the authored prompt constants
   (`agent_sdk/{cognition,expression}/lobes/`, `plugins/{safety,format}/lobes/`, `agent.py`
   `MEMORY_DIRECTIVE`, `plugins/planning` `_PLAN_PROMPT`): one role, no double negatives, an explicit
   directive, no ALL-CAPS shouting, bounded length. Emits `quality_avg`.
3. **judge** (live) — an LLM scores each prompt on clarity / specificity / consistency /
   output-contract (1–5). Gates on the **aggregate** `judge_mean` (the judge reads fragments out of
   context and is non-deterministic — a single low row is noise, not a gate). Run with `--trials` for
   a stable number.

## SDK target (the concept it maps to)
`agent_sdk/engine.py` (`_compose_system_segmented`, `_PROMPT_LAYERS`, `_layer_key`) and the authored
prompt constants across the lobe/plugin packages. Background: `docs/concepts/14-prompt-engineering.md`.

## The lever (optimization approach)
| Failing check | Root cause | Lever |
|---|---|---|
| `structure.*` | band/stability map wrong or a new source unmapped | `_PROMPT_LAYERS` / `_STAB_RANK` in `engine.py` |
| `quality.<prompt>` | the prompt violates a best practice (double negative, ALL-CAPS, multi-role, no directive) | rewrite that constant per `docs/concepts/14` (worked examples) |
| low `judge_mean` | the prompt corpus is genuinely weak (vague, contradictory, no output contract) | improve the lowest-scoring prompts (the per-prompt `weak` list) |

The layer order is **not** a tuning knob — it encodes the cache-prefix best practice; changing it is a
deliberate design change re-baselined with the prompt goldens (`tests/test_viewer.py`).

## Gate
`structure` + `quality` must `all_pass` (free); when `--live`, `judge_mean ≥ floor` (default 3.5).
Verdict `READY` iff all run tiers pass.
