# Method — delegationbench

> The optimization approach this bench drives. See `../_shared/TEMPLATE.md` for the standard.
> **Live-only** — no stubs, no fake/free tier (repo convention: benches run against the real
> provider). The deterministic fan-out engine invariants live in `agent_sdk/plugins/tasks/tests/`
> + `tests/test_planning.py`.

## What it certifies
Whether the agent **plans and executes well**: on rich, realistic, multi-faceted queries it routes
to the `plan` flow, writes a `TodoWrite` plan (one step per part), the supervisor picks a structure,
and **every planned piece gets solved** — by a subagent (`fanout`/`sequential`) or by the main agent
itself (`inline`) — then `fanin` aggregates — while on simple queries it stays single-shot. Each
subagent worker stays isolated, a failing one is survived, every facet lands in the answer (fan-in
fidelity), and no worker ever skips grounding (docs 08 + 12).

## The lever (optimization approach)
When NOT_READY, tune the smallest surface first:

| Failing dimension | Root-cause signal (from the probe trace) | Lever (surface to tune) |
|---|---|---|
| Under-planning (recall) | complex query scores below the flow threshold | `complexity_score` in `plugins/planning/path.py` (broaden verbs/list/enum gates) |
| Over-planning (precision) | a simple query routes to `plan` | tighten `complexity_score` |
| Routed right, didn't plan | `plan` ran but no `TodoWrite` call | the plan prompt (`plugins/planning/stages.py`) |
| Planned, piece unsolved | a fanout/sequential plan ran 0–1 subagents, or inline didn't answer | the supervisor (`plan_supervise` lobe) / `execute` map + inline wiring |
| Wrong structure | designed independent steps ran inline (or plain steps spawned subagents) | the `plan_supervise` decision (deps→sequential, designed→fanout, plain→inline) |
| Fan-in drops a facet | `fanin` misses a worker's result | the `plan_results` lobe + fanin prompt (`plugins/planning/`) |
| Isolation leak / lost worker | a worker sees another's chunks / a slow worker sinks the turn | `Stage.fanout_isolated` / bounded-failure path in `Engine._map_parallel` |

Hypothesis space: `agent_sdk/plugins/planning/` (the `TodoWrite` tool, the planning lobes, the
`plan → supervise → execute → fanin` stages + prompts, the `complexity_score` decision) and the
fan-out paths in `agent_sdk/engine.py`. Out of scope (never touched to pass): the gating dataset +
labels, the kernel dispatch, the pinned `cite`/`filter` grounding.

## Metrics & gates
Gating metrics decide the verdict; diagnostics are recorded but never gate.

| Metric | Direction | Gate (threshold) | Gating? |
|---|---|---|---|
| planning precision | higher | `>= 0.80` | gate |
| planning recall | higher | `>= 0.70` | gate |
| execution coverage | higher | `>= 0.70` | gate |
| fan-in fidelity | higher | `>= 0.70` | gate |
| avg plan steps / avg subagents | — | (report) | diagnostic |

`execution coverage` = of the should-plan cases that planned, the fraction whose pieces were all
solved — a subagent ran per todo for `fanout`/`sequential`, or the answer completed for `inline`.
Inline is a legitimate structure (the main agent solved the pieces), not a missing fan-out.

## Tier & dataset
- **live only** (`--live`, real provider): mount `PlanningPlugin()` (+ drop the `research` flow so a
  no-KB agent routes complex queries to `plan`), probe each scenario, and measure whether the agent
  plans when it should (precision/recall on the `TodoWrite` plan size), solves every planned piece
  (execution coverage — subagents for `fanout`/`sequential` via `blackboard["todos_results"]`, a
  completed answer for `inline`), and covers every facet (fan-in fidelity). No provider ⇒
  `UNMEASURED` (never a pass, never a fake fallback).
- Dataset: `dataset/scenarios.jsonl` — plan-worthy categories (`comparison` / `multi-entity` /
  `research` / `audit` / `planning`) each with `expect.delegate=true` + an `answer_contains` facet
  contract; `simple` + `near-neighbor` (look complex, are trivial) with `expect.delegate=false` to
  guard against over-planning.

## READY means
Run live, the agent plans with the required precision/recall, solves every planned piece (subagent
or inline), and meets the fan-in fidelity floor — i.e. it plans when (and only when) it pays off,
runs the plan in the right structure, and drops no facet.
