# fanoutbench

The arbiter for **subagent fan-out** (`docs/concepts/12-subagent-fanout.md`): a turn that splits
into N scoped workers, runs them parallel + context-isolated, survives a failing one, and aggregates
their memos — grounding the aggregate, never a worker's discretion.

```bash
python benchmarks/fanoutbench/run.py                         # free tier (deterministic, no provider)
python benchmarks/fanoutbench/run.py --live --report --label base   # + live fan-in fidelity
```

- **free** — exercises `Engine._map_parallel` / `_map_item_pool` under the `FakeClient`: isolation
  (zero cross-worker leakage), shared-pool parity, submission-order determinism, bounded failure,
  and `SubagentRegistry` resolution. Part of the free CI gate (`tests/test_subagents.py` mirrors it).
- **live** — decompose → fan-out → synthesize fidelity on multi-facet questions, with near-neighbor
  single-fact guards against over-fan-out.

Method, levers, and gates: [`METHOD.md`](METHOD.md). Standard: [`../_shared/TEMPLATE.md`](../_shared/TEMPLATE.md).
