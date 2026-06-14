# Planning (TodoWrite) — plan-driven fan-out: plan → supervise → fanout → fanin

> Multi-step work is the agent **planning with a tool**, a **supervisor** choosing the execution
> structure, and the engine **fanning out one subagent per planned step** before a fan-in
> aggregates. The plan tool mimics Claude Code's `TodoWrite` (which the model is trained to use);
> the plan *is* the spawn list. This is reasoning-as-a-tool
> ([`concepts/08`](../concepts/08-reasoning-as-a-tool.md)) wired into the engine's fan-out
> ([`concepts/12`](../concepts/12-subagent-fanout.md)).

> **Loop model.** A stage is `none` (pure prompt), `single` (one LLM call), `agentic` (a ReAct tool
> loop), or `map` (fan-out: one scoped sub-execution per work-item). The `plan` flow uses all four:
> `plan` (agentic) → `supervise` (none) → `execute` (map) → `fanin` (agentic).

## The capability

`PlanningPlugin` (`agent_sdk/plugins/planning/`) contributes:

- **`TodoWrite` tool** (`tool.py`) — `TodoWrite(todos=[{content, status, activeForm, prompt?,
  tools?, deps?}])`, the Claude Code shape extended so each todo is a *designed step*: its own
  `prompt` (how to do it), `tools` (what it needs), and `deps` (the 1-based indexes of todos it
  depends on). It **writes** the list to the turn's scratchpad (`scratchpad["todos"]`).
- **`todo_list` lobe** — renders the plan into context.
- **`plan_supervise` lobe** — the **supervisor**: reads the plan's shape and **writes**
  `scratchpad["plan_structure"]` — one of three, whichever it picks every piece still gets solved:
  `"sequential"` when any todo declares `deps` (subagent per todo, state-carry, in order),
  `"fanout"` when steps are independent **and designed** (each carries its own `prompt`/`tools` →
  one parallel, isolated subagent per todo), `"inline"` when steps are independent **and plain**
  (the main agent works the list itself in the execute stage — no subagent spawn). Pure function of
  the plan ⇒ deterministic routing (invariant #4).
- **`plan_results` lobe** — renders every subagent's result for the fan-in step.
- **`plan` flow** (`stages.py`) — `plan → supervise → execute → fanin`, grounded by the pinned
  `cite`/`filter`. A complex, multi-part query routes here via `path.py:complexity_score`; a simple
  query stays single-shot.

```python
from agent_sdk import PreactAgent
from agent_sdk.plugins.planning import PlanningPlugin

agent = PreactAgent(
    client=…, instructions="… plan multi-part tasks, fan out per step, then combine …",
    tools=[my_sql_tool],                         # the world tools the plan steps use
    plugins=[PlanningPlugin(worker_tools=["sql"])],
)
```

The model: writes a todo list (each step designed) → the supervisor picks the structure → the
engine runs **one subagent per todo** in that shape → the fan-in step aggregates → `cite`/`filter`
ground it. See the runnable [`examples/subagents-analytics/`](../../examples/subagents-analytics/)
(the agent plans 3 SQL analyses, fans out a subagent each, fans in an executive summary).

## How it works (reason → write → enact)

```
plan (agentic)            turn state                       supervise (none)        execute (map)
  TodoWrite(todos=[…]) ──write──▶ scratchpad["todos"] ──read──▶ plan_supervise ──write──▶ plan_structure
                                                          (deps→sequential · designed→fanout · plain→inline)
                                                                                                     │
                          scratchpad["todos_results"] ◀─write── fanout/sequential: one subagent per │
                                       │                          todo (scoped by its prompt/tools)  ◀┘
                          fanin (agentic) ◀── plan_results lobe   inline: the main agent works the
                                       │                          whole list itself in this stage
                                  answer → cite → filter
```

- The **engine** owns the execution: the `execute` stage (`loop="map"`) reads `plan_structure` and
  enacts it — `"inline"` runs ONE agentic loop where the main agent works the whole plan itself (the
  `todo_list` lobe keeps it in view); `"fanout"`/`"sequential"` run one scoped `_agentic`
  sub-execution per todo (parallel+isolated, or state-carry). A todo `{content, prompt, tools}` is a
  valid work-item directly (`content`→input/label, `prompt`→system_prompt, `tools`→the worker's
  tool slice). Whichever structure, every planned piece is solved and flows to `fanin`.
- The **supervisor** only writes data; the engine **enacts** it — `_fanout_with_structure` reads
  `plan_structure` for the subagent shapes, and the map dispatch short-circuits to a single agentic
  loop for `inline`. The default (no `plan_structure`) leaves the stage's own flags, so other `map`
  stages are unchanged.
- **Metacognition** can overwrite `plan_structure` (force a structure or replan) before `execute` —
  the same reason → write → enact seam.

No second interpreter, no separate `Subagent` tool — `TodoWrite` is an ordinary `ToolRuntime`, the
plan is the spawn list, and the deterministic lobe network + the engine's map dispatch run it.

## Research (general)

The default `research` flow is **general** (`flows/stages/research.py`): a single agentic
`investigate` stage over the agent's **full composed toolset** (no KB/RAG assumption — the SDK stays
domain-free), then `cite` → `filter`. A project (e.g. agent-core) mounts its own `kb.*` tools or a
KB-specific research flow on top.

## Invariants

- **Citations** — `cite`/`filter` stay pinned; the fan-in step grounds the aggregate, never a worker.
- **Deterministic routing** — `complexity_score` selects the `plan` flow; `plan_supervise` writes a
  structure that a deterministic enactor applies. No LLM judges the pipeline.
- **Per-subagent isolation + bounded failure** — fanout workers get a fresh evidence pool
  (`fanout_isolated`); a worker that raises/times out is recorded `status="failed"`, never sinks the
  turn.

## Benchmark

[`benchmarks/delegationbench/`](../../benchmarks/delegationbench/) is **live-only**: it measures the
whole loop — did the agent **plan** (`TodoWrite`) when (and only when) warranted (precision/recall),
**solve every planned piece** (execution coverage — subagent per todo for fanout/sequential, or a
completed answer for inline), and **cover every facet** in the combined answer (fan-in fidelity)?
