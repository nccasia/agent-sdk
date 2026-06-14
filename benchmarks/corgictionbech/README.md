# corgictionbech

The gate for the SDK's **metacognition** layer. Two tiers: a **deterministic floor** (free, no
provider) and a **live measurement of the equipped agent** (the best configuration; `--live`).

```bash
python benchmarks/corgictionbech/run.py            # deterministic floor (no provider)
python benchmarks/corgictionbech/run.py --report   # + results/corgictionbech.html
python benchmarks/corgictionbech/run.py --live --trials 2   # + the equipped-agent live tier
```

Deterministic modes: `monitor` (snapshots → observations), `regulate` (the decision table +
precedence), `pinned` (cite/filter escalate to meta_review, never skip), `channel` (apply/observe +
the action allowlist), and `plugin_surface` (the shipped `MetacognitionPlugin` assembles its
lobe/stages/flow/tool and its tool enactors write the right turn-state keys — matches the
implementation). Live (`--live`): run the equipped agent (`MetacognitionPlugin` + `apply`) on
`dataset/scenarios.jsonl` — gate on answer correctness, record `decision_hit_rate` (how often it
reaches for the expected meta lever; non-gating) + `meta_tokens_avg`. **Single-arm — measures the
best configuration, not a with-vs-without A/B.** See `METHOD.md`. Plugs into the `improve/` ratchet +
the `benchmarks/loop` ladder.
