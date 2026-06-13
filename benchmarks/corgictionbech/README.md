# corgictionbech

The deterministic gate for the SDK's **metacognition** layer (monitor→regulate, the apply/observe
channel, and the pinned-step guard: `cite`/`filter` are never skippable). Ported from the monorepo
onto the agent-sdk public surface; leaf-pure. **FREE / deterministic — no provider.**

```bash
python benchmarks/corgictionbech/run.py            # 18 deterministic checks
python benchmarks/corgictionbech/run.py --report   # + results/corgictionbech.html
```

Modes: `monitor` (snapshots → observations), `regulate` (the decision table + precedence), `pinned`
(cite/filter escalate to meta_review, never skip), `channel` (apply/observe + the action allowlist).
See `METHOD.md` for the levers. Plugs into the `improve/` ratchet + the `benchmarks/loop` ladder.
