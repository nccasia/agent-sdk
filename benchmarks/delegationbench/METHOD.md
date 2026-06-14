# Method — delegationbench

> The optimization approach this bench drives. See `../_shared/TEMPLATE.md` for the standard.

## What it certifies
Whether the agent **delegates well**: on rich, realistic, multi-faceted queries it reflects and
fans out to scoped subagents (decompose → handle each → aggregate), and on simple queries it stays
single-shot — then keeps each worker isolated, survives a failing one, and covers every facet in the
answer (fan-in fidelity), without ever letting a worker skip grounding (doc 12 + metacognition).

## The lever (optimization approach)
When NOT_READY, tune the smallest surface first:

| Failing dimension | Root-cause signal (from the probe trace) | Lever (surface to tune) |
|---|---|---|
| Under-delegation (recall) | complex query scores below the meta threshold | `complexity_score` / `make_recognize` in `plugins/metacognition/path.py` |
| Over-delegation (precision) | a simple query routes to `meta` | tighten `complexity_score` (verbs/list/enum gates) |
| Decided right, didn't fan out | `meta_reflect` ran but no `meta_control fan_out` | the reflect prompt (`plugins/metacognition/stages.py`); `subagent_catalog` lobe |
| Fan-in drops a facet | synthesize misses a worker's result | `synthesize` lobe / aggregate prompt; `meta_fanout` lobe slice |
| Isolation leak / lost worker | a worker sees another's chunks / a slow worker sinks the turn | `Stage.fanout_isolated` / bounded-failure path in `Engine._map_parallel` |

Hypothesis space: `agent_sdk/plugins/metacognition/path.py` (the decision signal),
`agent_sdk/subagents/`, the fan-out paths in `agent_sdk/engine.py`, the reflect prompt + subagent
`description`/`instructions`. Out of scope (never touched to pass): the gating dataset + labels, the
kernel dispatch, the pinned `cite`/`filter` stages.

## Metrics & gates
Gating metrics decide the verdict; diagnostics are recorded but never gate.

| Metric | Direction | Gate (threshold) | Gating? |
|---|---|---|---|
| decision precision (free) | higher | `>= 0.90` | gate (free) |
| decision recall (free) | higher | `>= 0.80` | gate (free) |
| isolation / bounded-failure / ordering (free) | — | hold | gate (free) |
| delegation precision (live) | higher | `>= 0.80` | gate (live) |
| delegation recall (live) | higher | `>= 0.70` | gate (live) |
| fan-in fidelity (live) | higher | `>= 0.70` | gate (live) |
| avg fan-out width (live) | — | (report) | diagnostic |

## Tiers & dataset
- **free** (deterministic, no provider): the **decision** — the complexity recognizer's
  precision/recall over the labeled dataset (`auto_delegate`) — plus the fan-out engine invariants
  (isolation, submission-order determinism, bounded failure) under `FakeClient`. Joins the free CI gate.
- **live** (`--live`, real provider): the **execution** — mount `SubagentsPlugin(auto_delegate=True)`,
  probe each scenario, and measure whether the agent actually fans out when it should (precision /
  recall on the `meta_control fan_out` call) and covers every facet (fan-in fidelity).
- Dataset: `dataset/scenarios.jsonl` — delegate-worthy categories (`comparison` / `multi-entity` /
  `research` / `audit` / `planning`) each with `expect.delegate=true` + an `answer_contains` facet
  contract; `simple` + `near-neighbor` (look complex, are trivial) with `expect.delegate=false` to
  guard against over-delegation.

## READY means
The decision recognizer hits its precision/recall floor and the fan-out invariants hold (free); and
when run live, the agent delegates with the required precision/recall and meets the fan-in fidelity
floor — i.e. it delegates when (and only when) it pays off, and drops no facet.
