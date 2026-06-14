# Method — fanoutbench

> The optimization approach this bench drives. See `../_shared/TEMPLATE.md` for the standard.

## What it certifies
Whether subagent fan-out (doc 12) holds its contract: a turn that decomposes into N scoped workers
fans them out, keeps each worker's context isolated, survives a failing worker, and aggregates the
memos faithfully — without ever letting a worker skip grounding.

## The lever (optimization approach)
When NOT_READY, tune the smallest surface first:

| Failing dimension | Root-cause signal (from the probe trace) | Lever (surface to tune) |
|---|---|---|
| Isolation leak | a worker sees another's chunks | `Stage.fanout_isolated` / `Engine._map_item_pool` |
| Lost worker | a slow/failing worker sinks the turn | bounded-failure path in `Engine._map_parallel`; per-item `timeout` |
| Non-deterministic output | parallel results reorder run-to-run | submission-order flush in `Engine._map_parallel` |
| Fan-in drops a facet | synthesize misses a worker's claims | `synthesize` lobe / the aggregate prompt; `meta_fanout` lobe slice |
| Over/under fan-out | named delegation not chosen / chosen wrongly | the reflect prompt + `subagent_catalog` lobe; subagent `description`s |

Hypothesis space: `agent_sdk/subagents/`, the fan-out paths in `agent_sdk/engine.py`, the
`plugins/{metacognition,subagents}/` wiring, subagent `description`/`instructions`.
Out of scope (never touched to pass): the gating dataset, the kernel dispatch, the pinned
`cite`/`filter` stages.

## Metrics & gates

| Metric | Direction | Gate (threshold) | Gating? |
|---|---|---|---|
| free checks pass | higher | all pass | gate |
| isolation (cross-worker leakage) | — | 0 | gate (free) |
| bounded failure (turn survives a bad worker) | — | true | gate (free) |
| ordering determinism | — | true | gate (free) |
| live fan-in fidelity (answer_contains) | higher | all pass | gate (live) |
| parallel_speedup | lower | (report) | diagnostic |

## Tiers & dataset
- **free** (deterministic, no provider): runs the engine fan-out under `FakeClient` and asserts
  isolation, shared-pool parity, submission-order determinism, bounded failure, and registry
  resolution (named resolve + unknown reject). These join the free CI gate.
- **live** (`--live`, real provider): decompose → fan-out → synthesize fidelity on multi-facet
  questions; near-neighbor single-fact questions guard against over-fan-out.
- Dataset: `dataset/scenarios.jsonl` — `fanout` multi-facet cases + `near-neighbor` single-fact cases.

## READY means
Every free check passes and (when run live) every live fan-in scenario meets its `answer_contains`
contract — i.e. no facet dropped, no worker leaked, no turn lost.
