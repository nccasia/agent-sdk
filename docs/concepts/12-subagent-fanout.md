# Subagent Fan-out

> A subagent explores in its own space and hands back a **memo** — never its raw working set.
> Fan-out runs N of them; fan-in keeps the conclusions, not the dumps.

> **Status: shipped (opt-in).** The generic `map` fan-out + research path are live; **plan-driven**
> subagents (the `TodoWrite` plan + a `plan → supervise → execute → fanin` flow), parallel +
> bounded-failure map, and true per-worker context isolation are shipped as opt-in surfaces (the
> default path is unchanged). **The plan is the spawn list** — there is no separate `Subagent` tool
> and no named registry. See *Implementation status*.

## Why subagents

A turn that does real work — research a question with five facets, run a ten-step plan, audit a large
surface — generates far more intermediate material (retrieved chunks, tool output, dead ends) than its
*answer* needs. Stuff all of it into one context and three things degrade at once: **recall rots** as the
window fills, the model can't **specialize** (one prompt, one tool belt, one budget for every facet), and
independent work runs **serially** when it could run at once.

A **subagent** fixes all three with one move: it does a slice of the work in a *separate* space, with its
*own* prompt / tools / model / budget, and returns only a **compressed result**. The parent keeps the
conclusion, not the exploration. Isolation is not an optimization detail — it is the primitive that makes
specialization and parallelism safe, because no worker can flood another's (or the parent's) context.

## The Claude Code model (what we mirror)

Claude Code's subagent system is the reference. The principles that matter here:

- **Context-window isolation.** Each subagent starts fresh — its own system prompt + the invocation
  prompt + its tool defs, *not* the parent's history. The two contexts never merge.
- **Return = summary, not dump.** A subagent may read fifty files (tens of thousands of tokens
  internally) and return a 1–2k-token summary. The parent sees the conclusion; the artifacts stay
  behind the boundary.
- **A composable, reusable definition.** A subagent is a declarative unit — `name`, `description`
  (when to delegate), `tools` (an allowlist), `model`, `prompt`. Define once, invoke many.
- **Parallel without shared state.** Several subagents run concurrently; they don't talk to each other —
  they report to the orchestrator, which aggregates.
- **Tool restriction is a feature.** A read-only reviewer with only `Read`/`Grep` *can't* mutate —
  least privilege per task, not a workaround.
- **The escalation ladder.** *subagents* (turn-by-turn delegation) → *agent teams* (peers with a shared
  task list) → *workflows* (a script orchestrating tens to hundreds). Each step moves orchestration
  further from the conversation context.

This is the **orchestrator-worker** pattern Anthropic uses in its multi-agent research system: a lead
plans and spawns workers, workers gather in parallel and return findings, the lead synthesizes.

## What the SDK has today (mapped to each principle)

The agent-sdk already implements the *hardest* parts of this model — compression and the
orchestrator-worker shape — through the `research` flow and the generic `map` loop. The honest scorecard:

| Claude Code principle | SDK mechanism today | Status |
|---|---|---|
| Orchestrator-worker shape | `research` flow = `plan → research → synthesize → cite → filter` (`agent_sdk/flows/defaults.py`) | live |
| Return = summary, not dump | the **`Memo`** (`agent_sdk/contracts/memo.py`) for research; the generic map's enriched result (`{label, result, status, tokens_used, error}`) for any worker | live (generalized) |
| Context isolation (boundary) | `Blackboard` rejects `RAW_CHUNK_KINDS` (`network/activation.py:113,494`) + `Stage.fanout_isolated` gives each worker a fresh evidence pool | live — output **and** execution boundary hold |
| A composable subagent definition | the **`TodoWrite` plan** (`plugins/planning/`) — each todo `{content, prompt, tools, deps}` *is* a subagent definition → a map work-item | live (plan-driven; no named registry) |
| Tool restriction per worker | a todo's `tools` filters the worker's specs; `PlanningPlugin(worker_tools=…)` sets the worker belt | live |
| Parallel without shared state | the research lobe's `asyncio.gather`; `Stage.fanout_parallel` for the *generic* map (semaphore-bounded, bounded-failure) | live (generalized) |
| Reusable, model-tiered, restricted | per-item `model`/`hops`/`tools` overrides on the map work-item | live |

```txt
        ORCHESTRATOR                 WORKERS (fan-out)                FAN-IN
        ────────────                 ─────────────────                ──────
                              ┌──▶ subagent[aspect 0] ──Memo──┐
   plan ──decompose──▶ work-  ├──▶ subagent[aspect 1] ──Memo──┤──▶ synthesize ──▶ cite ──▶ filter
   (sub_questions)     list   ├──▶ subagent[aspect 2] ──Memo──┤    (claims only)   (pinned, never
                              └──▶ subagent[aspect N] ──Memo──┘                     a worker's call)
                                        │                                  ▲
                          raw chunks ───┘  confined to each worker's       │ only Memo-shaped
                                           receptive field — NEVER cross ──┘ objects cross
```

## The concept: a Subagent

A **subagent** is a scoped, bounded sub-execution defined by `(prompt, tools, lobes, model, budget)` that
returns a **compressed memo**, never its raw context. **Fan-out** runs one subagent per item of a
work-list; **fan-in** aggregates their memos into the next stage.

In the SDK this is the `map` loop. A stage with `loop="map"` and a `fanout_key` reads
`scratchpad[fanout_key]` — a list a prior stage filled — and runs one bounded `_agentic` sub-execution per
item (`Engine._map_stage`, `engine.py:1324`). **Each work-item dict *is* an ad-hoc subagent definition:**

```python
# what a map item can override today — name these fields and you have a Subagent:
{ "id": "...", "input": "...",        # identity + the worker's task
  "system_prompt": "...",             # its own prompt (defaults to _MAP_ITEM_PROMPT)
  "tools": [...], "lobes": [...],     # its own (restricted) tool/lobe belt
  "model": "...", "max_tokens": 1024, "hops": 12 }   # its own model + budget
```

A **`TodoWrite` plan** produces exactly these work-items: each todo `{content, prompt, tools, deps}`
maps onto a subagent definition (`content`→input/label, `prompt`→system_prompt, `tools`→the worker's
belt) — so the plan *is* the spawn list and `loop="map"` over `scratchpad["todos"]` runs one subagent
per todo. The research flow's decomposer (`cognition/lobes/plan.py::run`) is the other producer: it
turns a query into 2–5 aspects; the worker (`research.py::run_aspect`) runs a bounded retrieval ReAct
loop and returns a `Memo` (`aspect_id` + `claims[]` each with `supporting_chunk_ids` + `unresolved` +
`tokens_used`). The `Memo` *is* the "summary, not dump" contract: claims and citations cross the
boundary; the chunks they stand on do not.

## Two fan-out shapes

The same concept has two execution shapes; pick by whether the workers depend on each other:

```txt
SEQUENTIAL (state-carrying)              PARALLEL (independent, map-reduce)
loop="map" — Engine._map_stage           asyncio.gather — research lobe
item i sees items 0..i-1 as notes        items run at once, semaphore-bounded (fanout_max)
use when: steps build on each other      use when: facets are independent
          (a plan rail, a pipeline)                (research aspects, an audit)
wall-clock = sum of items                wall-clock = slowest item
```

The engine's generic `map` is **sequential by default** — it carries each worker's result forward as a
note so the next worker can build on it (the `tasks`-plugin rail relies on this). `Stage.fanout_parallel`
selects the **parallel** shape (semaphore-bounded `gather` with per-item failure isolation), the research
lobe's shape generalized to any map stage. The two shapes now differ only by a flag, not by which
subsystem you use.

## Context isolation + the compression invariant

The unifying rule, and the SDK's strongest existing guarantee: **only memo-shaped objects cross a
subagent boundary; raw chunks never do.** `Blackboard._add` raises if a node's `kind` is in
`RAW_CHUNK_KINDS = {"kb_chunk","raw_chunk"}` (`network/activation.py:494`) — raw retrieved chunks are
confined to the producing worker's receptive field and may not join the shared pool (prd.md §10). That is
exactly Claude Code's "return a summary, not the artifacts," enforced *structurally* rather than by
prompt discipline.

The worker's *execution* is isolated on demand: by default every `map` sub-execution shares the turn's
evidence channel (`retrieved_chunks` / `already_read`), but `Stage.fanout_isolated` gives each worker a
**fresh** pool (`Engine._map_item_pool`) — worker A's chunks never enter worker B's window, and only its
result crosses back. With both the output boundary (memos only) and the input boundary (fresh window)
clean, an isolated parallel worker *is* a Claude-Code subagent. The `plan` flow's supervisor turns
both on (it writes `plan_structure="fanout"` for an independent plan, which the engine enacts as
parallel + isolated).

## The escalation ladder (mapped to Claude Code)

```txt
  single agentic loop   →   in-turn fan-out (map)   →   isolated subagents      →   recursive / workflow
  one worker, one ctx       N workers, shared pool      N workers, fresh ctx        nested fan-out, many
  (loop="agentic")          (loop="map", LIVE)          summary-only (LIVE,         agents (DEFERRED)
                                                        fanout_isolated)
        Claude Code:  ── subagents ──────────────────▶  ── teams ──▶  ── workflows ──▶
```

The SDK now reaches "isolated subagents" (fresh evidence pool + summary-only return, via
`fanout_isolated`), driven by the `TodoWrite` plan (one subagent per todo). Beyond that, nested maps
and recursive `PreactAgent` sub-agents map onto Claude Code's teams/workflows tier (still deferred).

## Implementation status

This builds on machinery that already exists; the default path is unchanged.

**Live today.**
- Generic fan-out — `Stage.loop="map"` + `Stage.fanout_key` (`agent_sdk/stages.py:51-53`),
  `Engine._map_stage` (`engine.py:1324`): per-item scoped `Stage`, tool/lobe/model/budget overrides,
  state-carry via notes, results → `scratchpad[fanout_key + "_results"]`, empty-list degrades to one
  agentic run (parity), bounded to 40 items.
- Decompose → fan-out → fan-in — the `research` flow (`flows/defaults.py`): `plan` decomposes
  (`cognition/lobes/plan.py`), `research` fans out in parallel (`cognition/lobes/research.py::run`,
  `asyncio.gather` + semaphore), `synthesize`/`cite`/`filter` aggregate and ground.
- The compression boundary — `Memo`/`Claim`/`Citation` (`contracts/memo.py`) + `Blackboard`
  raw-chunk rejection (`network/activation.py`).
- A second map user — the `tasks` plugin's TodoRail → `loop="map"` over `todos`
  (`agent_sdk/plugins/tasks/`).
- The work-list accessor — `Scratchpad.as_list` (`memory/scratchpad.py`).

**Live today (opt-in; default path unchanged).**
- **Plan-driven subagents** — `PlanningPlugin` (`plugins/planning/`) exposes the `TodoWrite` tool;
  the model writes a plan (one designed todo per part), and the `execute` stage (`loop="map"`,
  `fanout_key="todos"`) runs one subagent per todo. **The plan is the spawn list** — no separate
  `Subagent` tool, no named registry. Each todo `{content, prompt, tools, deps}` scopes its worker.
- **The `plan → supervise → execute → fanin` flow** (`plugins/planning/stages.py`): `supervise` is a
  deterministic `loop="none"` step whose `plan_supervise` lobe reads the plan's deps and writes
  `scratchpad["plan_structure"]` (`"fanout"` for independent steps, `"sequential"` when any todo has
  `deps`); the engine's `_fanout_with_structure` enacts it. `fanin` is agentic with the
  `plan_results` lobe rendering every worker's `{label, status, result}` for the model to aggregate.
- **Parallel + bounded-failure** for the *generic* map — `Stage.fanout_parallel` runs items via
  `asyncio.gather` bounded by `Stage.fanout_max` (≤ 40), events buffered + flushed in item order
  (deterministic); a worker that raises or exceeds a per-item `timeout` is recorded
  `status="failed"`, never dropped, never sinks the turn (`Engine._map_parallel`, `engine.py`).
- **True per-worker context isolation** — `Stage.fanout_isolated` gives each worker a fresh
  `retrieved_chunks`/`already_read`; worker A's chunks never enter worker B's window, only its
  result returns (`Engine._map_item_pool`).
- **Deterministic routing** — `complexity_score` (`plugins/planning/path.py`) selects the
  `plan` flow on multi-faceted queries; no LLM judges the pipeline.

**Deferred.**
- Nested maps (a worker that itself fans out — `TodoWrite` is kept out of workers).
- Recursive `PreactAgent`/`Engine` subagents (a real child run, not a scoped stage).
- A predefined/named subagent registry + automatic delegation by `description` (a prior iteration
  shipped this; removed in favor of the plan-driven model — re-add if a use case needs it).
- Inter-worker messaging (the "teams" tier).

## Boundaries (keep it the same engine, not a fork)

- **No second interpreter.** Subagents reuse `Engine._agentic` with a scoped `Stage` — not a parallel
  runtime. New capability is a stage flag / item field, never a kernel branch.
- **Citations carve-out.** `cite`/`filter` are pinned stages that run on the *aggregated* memos — never
  inside a worker's discretion. A subagent cannot decide to skip grounding; ground-or-refuse is the
  flow's, not the worker's.
- **Budgets are explicit.** ≤40 items per map, `fanout_max` + semaphore on parallel fan-out, per-item
  `hops`/`max_tokens`. Fan-out widens work, never the safety envelope.
- **Routing is deterministic.** *Which* stage fans out (and the flow selected) is signal-driven, not an
  LLM judging the pipeline — consistent with the network's determinism invariant.

## Benchmarking

`agentbench` and `taskbench` already exercise the `map` path (plan-builds-rail-then-map-runs-each-item).
**`benchmarks/delegationbench/`** is the dedicated, **live-only** slice: it gates the **execution**
(real planning precision/recall + fan-out coverage + fan-in fidelity) on the real provider, and its
report shows each subagent's own timeline. The deterministic fan-out engine invariants (isolation /
bounded failure / ordering) are unit-tested in `agent_sdk/plugins/tasks/tests/` + `tests/test_planning.py`.
The properties:

```txt
Planning precision    plan (TodoWrite) only when it pays off                 → live, ≥ floor
Planning recall       plan on the should-plan cases                          → live, ≥ floor
Fan-out coverage      one subagent per todo on the plan cases                → live, ≥ floor
Fan-in fidelity       facets surviving plan → fan-out → synthesize           → ≥ floor (no facet dropped)
Isolation             no cross-worker leakage (worker A's chunks in B)       → 0  (unit-tested)
Bounded failure       one slow/failing worker doesn't sink the turn          → degrade (unit-tested)
```

## Related

- [Tool Use at Scale](./05-tool-use-at-scale.md) — the memo/result compression + read-back machinery a
  worker's body relies on (this doc applies it across a *boundary*, not within one loop).
- [Universal Memory](./06-universal-memory.md) — where a worker's memo persists / is selected back in.
- [Task Execution Mode](./13-task-execution-mode.md) — the long-rail TodoRail that drives `loop="map"`
  sequentially.
- [Architecture](./01-architecture.md) — the OX/OY surface the `research` flow rides; `Engine._map_stage`
  is one stage's loop mode.
- [Intent and Paths](./02-intent-and-paths.md) — how the `research` (fan-out) path is recognized vs `qna`.
- [Metacognition](./11-metacognition.md) — the capacity module whose meta-control tool can decide *whether
  and how* to fan out subagents (and can itself be granted to a subagent).

## Design principle

A capable agent is not the one that does everything in a single context, nor the one that forks blindly.
It is the one that sends a **scoped worker to think in its own space** and brings back the **memo, not the
mess** — so the whole stays small no matter how wide the work fans.
