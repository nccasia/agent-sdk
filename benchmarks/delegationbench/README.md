# delegationbench

The arbiter for **delegation** — how well the agent uses metacognition + subagent fan-out
(`docs/concepts/12-subagent-fanout.md`). It runs rich, realistic, multi-faceted queries that a
single agent answers worse than a delegating one, and grades the **decision** (delegate when, and
only when, it pays off) and the **execution** (fan out, cover every facet, stay isolated).

```bash
python benchmarks/delegationbench/run.py                              # free tier (no provider)
python benchmarks/delegationbench/run.py --live --report --label base # + live delegation behavior
```

- **free** — the *decision*: the complexity recognizer's precision/recall over the labeled dataset
  (`auto_delegate`), plus the fan-out engine invariants (isolation, ordering determinism, bounded
  failure) under the `FakeClient`. Part of the free CI gate (`tests/test_subagents.py` mirrors it).
- **live** — the *execution*: mount `SubagentsPlugin(auto_delegate=True)`, probe each scenario, and
  measure real delegation precision/recall (did it call `meta_control fan_out` when it should?) and
  fan-in fidelity (did every facet land in the answer?).

Dataset categories: delegate-worthy `comparison` / `multi-entity` / `research` / `audit` /
`planning`; single-agent `simple` / `near-neighbor` (look complex, are trivial) as over-delegation
guards. Method, levers, and gates: [`METHOD.md`](METHOD.md). Standard:
[`../_shared/TEMPLATE.md`](../_shared/TEMPLATE.md).
