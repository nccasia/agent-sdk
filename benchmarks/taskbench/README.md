# taskbench — plan a complex task into a flow, drive it to completion

**Question:** how well can the SDK take a goal, *plan* it into a flow of stages, and *drive* that
flow end-to-end until the work is actually done?

A **task** is a structural unit: a goal + an ordered checklist of steps (todos). In this bench:

- the **flow / stages are the DRIVER** we grade (the OX axis),
- the **todos are ARTIFACTS** the run emits + advances (`todos.add` / `todos.update`),
- the mock store's **`state` is the verifiable end-state** the scorer checks (not just "boxes ticked").

Two kinds cut across every capability:

- **predefined** (well-known) — a known task → a known flow (`run_known = execute → verify`); the
  checklist is *seeded*, the agent recognizes it and drives the known rail.
- **on-the-fly** — a bare novel goal → `do_task = plan → execute → verify`; the `plan` stage must
  *construct* the rail first.

## Forcing function, not a victory lap

taskbench targets the **real difficulty** of complex-task agency and grades each capability
`READY / NOT_READY / UNMEASURED` — so the gaps are an honest roadmap, never a silent pass. The
capability matrix:

| # | capability | how it's measured |
|---|---|---|
| 1 | decompose | dynamic case builds ≥N todos covering the required steps |
| 2 | drive to completion | every todo reaches `done` (checklist drained) |
| 3 | state carry | an early step's output feeds a later one (verified `state`) |
| 4 | tool orchestration | the right `work.*` tool per step → correct `state` |
| 5 | predefined fast-path | routes to `run_known`, no spurious planning |
| 6 | dependency order | store refuses out-of-order `done`; task still completes |
| 7 | parallel fan-out | **UNMEASURED** — `loop="map"` runs sequentially in the engine |
| 8 | branching | the agent records the branch the condition selects |
| 9 | replan | a todo is added *during* execution, then completed |
| 10 | error recovery | an injected transient failure is retried to `done` |
| 11 | long-horizon | **UNMEASURED** — multi-fire continuations aren't in the SDK core |

Caps 7 and 11 read `UNMEASURED` today (structural SDK gaps). Closing them — wiring real fan-out, a
`replan` metacog action, continuation loops — flips those rows; that's the point.

## Run

```bash
python run.py --replay                 # FREE: scripted solver, no provider (CI floor)
python run.py --live                    # LIVE: real provider plans + drives each case
python run.py --live --trials 3         # aggregate over trials
python run.py --live --capability 6     # only one capability's cases
```

Exit `0` = verdict `READY`, `1` = not ready, `2` = setup error. Reports land in `results/`.

## Layout

- `tasks_tool.py` — the mock `tasks.*` runtime + `work.*` tools (the structural unit; deps
  enforcement + deterministic failure injection + verifiable `state`).
- `agent.py` — `build_task_agent(client, scenario)`: the two driver flows + stages, reusing the SDK's
  generic lobes and the agentic-loop budgets (`stall_patience`, `enforce_tool_allowlist`).
- `fakes.py` — the scripted solver for the free tier.
- `dataset/{predefined,dynamic}.jsonl` — capability-tagged scenarios with verifiable `expected_state`.
- `run.py` — scores each case → a capability matrix verdict.
- `test_taskbench.py` — deterministic unit tests for the store + scorer.
