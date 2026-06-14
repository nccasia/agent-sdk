# Subagents analytics example

A worked example of **plan-driven subagent fan-out** (see
[`docs/sdk/planning.md`](../../docs/sdk/planning.md)): the agent **plans** a multi-part analytics
question into a todo list (one designed step per analysis), a **supervisor** sees the steps are
independent and picks **fanout**, the engine spawns one **context-isolated subagent per todo** (each
running its own SQL against a SQLite fixture), then the **fan-in** stage aggregates every subagent's
result into an executive summary.

```
question ─▶ PLAN ─▶ SUPERVISE ─▶ EXECUTE (one subagent per todo, isolated)        FANIN
            TodoWrite   (deps? →   t0 "top products"  → worker → sql(GROUP BY product) ─┐
            3 todos      fanout)   t1 "by region"     → worker → sql(GROUP BY region)  ─┼▶ plan_results()
                                   t2 "monthly trend" → worker → sql(GROUP BY month)   ─┘   → executive summary
```

## Run it

```bash
# offline + deterministic — scripted reasoning, but the SQL is REAL (runs on the SQLite fixture)
python examples/subagents-analytics/demo.py

# live — a real model decides the sub-analyses, writes the SQL, and writes the summary
python examples/subagents-analytics/main.py            # needs a provider token
```

The `demo.py` output shows the `plan → supervise → execute → fanin` stages, the three isolated
`sql(...)` runs (one subagent per todo), and a final summary built from the **real** computed
numbers (top product, leading region, monthly trend).

## How it's wired (≈3 lines on the public surface)

```python
from agent_sdk import PreactAgent
from agent_sdk.plugins.planning import PlanningPlugin
from analytics.fixture import SqlToolRuntime, build_db

agent = PreactAgent(
    client=...,                               # any LLM client (or the scripted FakeClient in demo.py)
    instructions="You are a data analyst with a `sql` tool … plan it, fan out per analysis, combine.",
    tools=[SqlToolRuntime(build_db())],       # the read-only SQL tool the workers use
    plugins=[PlanningPlugin(worker_tools=["sql"])],   # TodoWrite + plan → supervise → fanout → fanin
)
```

- **`PlanningPlugin(worker_tools=["sql"])`** exposes the `TodoWrite` planning tool and the
  `plan → supervise → execute → fanin` flow, and hands each per-todo subagent the `sql` tool (each
  todo's own `tools` narrows it further). **The plan is the spawn list** — there is no separate
  `Subagent` tool; the engine runs one subagent per todo.
- A deterministic complexity signal routes the multi-part question to the `plan` flow; a simple
  one-fact question would stay single-shot.
- The **supervisor** (`plan_supervise` lobe) reads the plan's deps and writes
  `scratchpad["plan_structure"]` — `fanout` for independent steps (parallel, isolated), `sequential`
  when a todo declares `deps`. Each worker runs in its **own isolated context** — one worker's rows
  never leak into another's — and returns only its finding. `cite`/`filter` ground the aggregated
  answer, never a worker.

## Files

| File | What it is |
|---|---|
| `analytics/fixture.py` | the in-memory SQLite `sales` table (deterministic, trend-bearing) + the read-only `sql` `ToolRuntime` |
| `analytics/agent.py` | `build_analytics_agent(conn, client)` — the `sql` tool + `PlanningPlugin` |
| `analytics/fakes.py` | the scripted model that drives the offline demo (plan 3 → fanout SQL → aggregate) |
| `demo.py` | offline, deterministic run (real SQL, scripted reasoning) |
| `main.py` | live run (real model) |
