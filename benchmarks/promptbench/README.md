# promptbench — are the SDK's prompts good? (FREE structure+quality · LIVE judge)

Evaluates the SDK's prompts on three tiers:

- **structure** (free, deterministic) — the composed system prompt is well-layered: a stable,
  cacheable instruction prefix leads and the turn-volatile sections form a contiguous tail (the
  conversation + query own recency in the `messages` array that follows). Identity appears once and
  first, `<env>` is last, no section/persona/conversation duplication. Read off the probe's
  `system_segments` (no LLM).
- **quality** (free, deterministic) — a rule-based lint of the SDK's authored prompt constants
  against the best practices in `docs/concepts/14-prompt-engineering.md`: one role only, no double
  negatives, an explicit output/action directive, no ALL-CAPS shouting, bounded length. Emits a
  per-prompt `quality_avg`.
- **judge** (live, `--live`) — an LLM scores each authored prompt on a rubric (clarity / specificity
  / consistency / output-contract, 1–5). It reads each prompt **out of context**, so it gates on the
  **aggregate** `judge_mean`, not per-prompt; the per-prompt rows show *where* prompts are weak.

```bash
python benchmarks/promptbench/run.py            # free tiers (structure + quality)
python benchmarks/promptbench/run.py --live      # + the LLM-judge tier
python benchmarks/promptbench/run.py --report     # + results/promptbench.html
```

Verdict `READY` iff every tier passes (judge only when `--live`). The free tiers gate in the no-cred
ladder; the judge is a live measurement (run with `--trials` for stability — the model is
non-deterministic). Method + levers: `METHOD.md`. Background: `docs/concepts/14-prompt-engineering.md`.
