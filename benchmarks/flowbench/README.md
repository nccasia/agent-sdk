# flowbench

The bench for the SDK's **flow axis** (OX): each intent resolves to the right named flow + step
sequence, grounded paths carry the cite→filter contract, routing is deterministic, and an
unrecognized turn degrades to a graceful fallback. Read via the no-LLM `PreactAgent.inspect`.
**FREE / deterministic.**

```bash
python benchmarks/flowbench/run.py            # 12 deterministic checks
python benchmarks/flowbench/run.py --report   # + results/flowbench.html
```

Modes: `sequence`, `grounded`, `determinism`, `fallback`. See `METHOD.md`. Plugs into the `improve/`
ratchet + the `benchmarks/loop` ladder.
