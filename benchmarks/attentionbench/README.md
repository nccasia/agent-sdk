# attentionbench

The bench for the SDK's **context axis** (OY): node selection (relevant outranks flooders, traps drop
below the floor — `score_relevance`) and lobe activation (recall always-on, grounding lobes on
grounded paths and absent on social turns, the reply lobe always). Read via the no-LLM
`PreactAgent.inspect`. **FREE / deterministic.**

```bash
python benchmarks/attentionbench/run.py            # 14 deterministic checks
python benchmarks/attentionbench/run.py --report   # + results/attentionbench.html
```

Modes: `select`, `recall`, `grounding`, `reply`, `determinism`. See `METHOD.md`. Plugs into the
`improve/` ratchet + the `benchmarks/loop` ladder.
