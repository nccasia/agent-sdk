# delegationbench

The arbiter for **plan-driven execution** — how well the agent plans a task and runs it in the right
structure (`docs/concepts/12-subagent-fanout.md`, `docs/sdk/planning.md`). **Live-only** (no stubs,
no fake data): it runs rich, realistic, multi-faceted queries against the real provider and grades
the **decision** (plan when, and only when, it pays off) and the **execution** (write a `TodoWrite`
plan, then solve every piece — by a subagent for `fanout`/`sequential`, or inline in the main stage —
covering every facet, staying isolated).

```bash
python benchmarks/delegationbench/run.py --live --report --label base   # needs a provider token
python benchmarks/delegationbench/run.py --live --model claude-opus-4-8
```

Mount `PlanningPlugin()` (+ drop the `research` flow so a no-KB agent routes complex queries to
`plan`), probe each scenario, and measure real planning precision/recall (did it write a plan when
it should?), execution coverage (was every planned piece solved — a subagent per todo for
`fanout`/`sequential` via `blackboard["todos_results"]`, or a completed answer for `inline`?), and
fan-in fidelity (did every facet land in the answer?). Each scenario's real probe is written to the
report — the Prompt tab shows each turn's rendered plan and the per-todo subagents. No provider ⇒
`UNMEASURED`.

The deterministic fan-out engine invariants (isolation / ordering / bounded failure) are unit-tested
in `agent_sdk/plugins/tasks/tests/` + `tests/test_planning.py`, not here.

Dataset categories: plan-worthy `comparison` / `multi-entity` / `research` / `audit` / `planning`;
single-agent `simple` / `near-neighbor` (look complex, are trivial) as over-planning guards. Method,
levers, and gates: [`METHOD.md`](METHOD.md). Standard: [`../_shared/TEMPLATE.md`](../_shared/TEMPLATE.md).
