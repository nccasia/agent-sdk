# Task-focused execution mode (design)

> **Status: design / direction.** This doc maps a long-running **task execution**
> mode onto the existing engine (OY lobes, OX flows, metacognition) and the
> context-memory store. It is the engine-side counterpart to the benchmark split:
> [`taskbench`](../../../benchmarks/taskbench/) measures execution quality,
> [`schedulebench`](../../../benchmarks/schedulebench/) measures create/manage/
> schedule assistance. Nothing here changes behavior yet; it defines what to
> build and what taskbench should measure as it lands.

## Why a distinct mode

Assist turns and task execution are *almost* the same engine, but their pressures
differ. An assist turn is short, interactive, and human-paced: the user is in the
loop, context is one conversation window, and "done" is one good answer. A task
execution is **long-rail and autonomous**: it fires on a schedule (or is claimed
by an external worker — see the assistant/executor split in
`apps/services/tasks.fire_task` and `apps/worker-task`), runs across **many
continuation fires** (`max_continuations`), advances a `bot_task_todos`
checklist, and must not lose the thread between fires. The same lobes/flows can
serve it, but the runtime needs more tweaks for: durable self-context, a stable
plan it can track across fires, per-step context discipline, defect/error
handling, progress reporting, and the ability to pause and ask for help.

This is the `task_execute` flow today (`[task_execute_advance, task_execute_format]`,
`agent_core/flows/defaults.py`) grown up.

## The execution turn on the OX/OY surface

A task-execution turn is one point on the same OX/OY surface as an assist turn
(see [architecture.md](./01-architecture.md)), entered when the interpreter sees the
`[Scheduled task execution]` fired-prompt header (`fired_prompt` signal) and the
`task_execute` path is recognized.

```
OX (task_execute flow):  recall_state -> plan/advance -> act -> checkpoint -> report -> format
OY slice per step:       task_execution, memory_recall  (+ research/synthesize/tools as needed)
```

The seven capabilities the user asked for, each mapped to an existing primitive
rather than a new subsystem:

| Capability | Where it lives | What to build |
|---|---|---|
| **Self context-management** (note decisions / variables / intent; read back over a very long task without losing context) | the `memory` tool + `context_entries`, **`conversation` scope** ([universal-memory.md](./06-universal-memory.md)) | A task **scratchpad convention**: the `task_execution` lobe encourages `memory{save, scope:"conversation", key:"decision_*/var_*/intent", ttl_days:N}` during `advance`, and the `## Memory` index surfaces them on the next fire. The conversation scope is the natural task-instance bucket (one task ↔ one conversation_id). No new store. |
| **Long-rail plan / DSL** (a workflow translated into phrases that track on a long rail) | the `bot_task_todos` checklist + the rich `prompt` (Goal/Context/Steps/Output) | Treat the todo list as the **execution rail**: each step is a todo with `status` ∈ todo/doing/blocked/done. A small declarative step grammar (a "task DSL") compiles the prompt's Steps section into todos with `tool_hint`s, so the engine tracks position on the rail instead of re-reading free text each fire. |
| **Per-step context awareness** | OX flow steps choose their OY lobe slice | `task_execute_advance` already scopes tools; extend so each rail step pulls only its relevant context node (its todo + the scratchpad keys it references), not the whole task. Metacognition's `adjust_lobe_slice` trims under pressure. |
| **Unexpected defects / error handling** | run status state machine (`RUN_TRANSITIONS`) + todo `status="blocked"/"failed"` + metacognition | On a step error: mark the todo `blocked`/`failed` with `error`, write a scratchpad note, and let metacognition decide `retry_step` (bounded) vs escalate. The continuation loop re-fires; the next fire reads the failure note and changes direction. |
| **Report progress back to the user** | trace events (`partial`) + `mezon:outbound` delivery | A `report` step emits a progress line per fire (what advanced, what's blocked, what's next) without ending the task — distinct from the final `format`/deliver. |
| **Pause to ask for help** | `BotTaskHumanRequest` + run status `waiting_for_human` + `todos.request_human` tool | When a step is `blocked` on a human decision, call `todos.request_human`, move the run to `waiting_for_human`, and stop firing until answered — the answer feeds back in as context on resume. |
| **Feedback / change direction on the fly** | scratchpad + checklist mutation (`todos.add`/`update_todo`) | A human reply or a discovered fact rewrites the rail: add/skip/reorder todos and record the intent change as a scratchpad decision, so the redirect survives to the next fire. |

## The self-context loop (the core idea)

The thing that makes a *very long* task survivable is that the engine writes its
own working state to durable memory and reads it back, instead of relying on the
turn window (which resets each fire). Concretely, per fire:

```
1. recall_state : load this task instance's scratchpad (conversation-scope memory)
                  + the open todos + the last run's result (run memory).
2. advance      : pick the next todo; do the work (agentic, scoped tools);
                  WRITE BACK decisions/variables/intent as memory{save,conversation}.
3. checkpoint   : update todo status (doing→done/blocked/failed) + the run result.
4. report       : emit a progress line (trace partial).
5. continue?    : open todos + progress → continuation fire; blocked-on-human →
                  request_human + waiting_for_human; all done → complete + deliver.
```

Because steps 1 and 2 round-trip through `context_entries` (conversation scope,
isolated server-side by `conversation_id`), the task keeps a coherent memory of
its own decisions across an arbitrary number of fires — the "don't lose context
on a long-running task" requirement — with **zero raw-chunk leakage** (only
memo-shaped notes cross the boundary, honoring the compression invariant).

## What to build, in order (each gated by taskbench)

1. **Scratchpad convention** — ✅ *landed (Phase 1).* The `task_execution` lobe
   emits a pinned `task_exec:scratchpad` node (persist decisions/variables/intent
   to conversation-scoped memory; read it back from the `## Memory` block), and
   the `task_execute` advance step now carries the `memory` tool in its allowlist
   (without it the scratchpad is hard-filtered out). Reads come free via the
   per-turn `## Memory` index (conversation scope, isolated by `conversation_id`).
   Seeds: `tests/lobes/test_task_scratchpad.py`. *Next:* the LLM gate
   `taskbench --mode context_retention` — seed a multi-fire task, assert a
   decision written on fire *k* is present in context on fire *k+1*.
2. **Rail/DSL** — compile the prompt's Steps into todos; `taskbench` asserts the
   engine advances by rail position (todo-by-todo), not re-derivation. Extends the
   existing `todoloop` mode.
3. **Report + pause** — `report` step + `request_human`/`waiting_for_human`
   resume; `taskbench --mode progress_and_pause` asserts a progress line per fire
   and a clean pause→answer→resume cycle.
4. **Error handling / redirect** — inject a step failure; assert block→note→
   retry-or-escalate and that a human redirect rewrites the rail. New `taskbench`
   cases under `execquality`/a `recovery` mode.

## Boundaries (keep it the same engine, not a fork)

- **No second interpreter.** This is the `task_execute` flow + the
  `task_execution` lobe + metacognition, tuned — not a parallel runtime. New
  capability = a lobe/flow-step/registry row, never an interpreter branch
  (architecture.md "Extension Rules").
- **Citations.** Execution output is often non-RAG (it ran a tool, advanced a
  checklist). The `refuse_if:no_citations` filter is for grounded *answers*; an
  execution `report`/deliver is tool output. This carve-out is the same open
  question flagged for external-worker output — resolve it once, consistently.
- **Budgets.** Long tasks are bounded by `max_continuations` and per-layer flow
  budgets; the scratchpad has `ttl_days`. Unbounded growth is a bug, gated by
  `todoloop`'s cap check.
- **Identity/ACL.** Fires inherit the creator's identity; the scratchpad's
  `scope_ref` is server-resolved from `conversation_id`. The model never supplies
  identity (universal-memory.md).

## Related

- [architecture.md](./01-architecture.md) — OX/OY/metacognition the mode reuses.
- [universal-memory.md](./06-universal-memory.md) — the scratchpad store + scopes.
- [subagent-fanout.md](./12-subagent-fanout.md) — the `loop="map"` fan-out the TodoRail drives, and the
  subagent/compression model behind it.
- `benchmarks/taskbench/` — execution gate (where each step above is measured).
- `apps/worker-task/`, `apps/worker-local/` — the in-house and external executor
  tiers that run this flow off the assistant queue.
