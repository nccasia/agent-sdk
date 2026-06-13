# agent-core: the reusable assistant engine

`agent-core` owns the object-level thinking loop: context selection, tool use,
and final-answer shaping. It also owns the meta layer that observes and regulates
those steps. The worker app is only a deployment shell; the behavior lives here.

The current architecture has two independent optimization axes:

- **OY context:** what context fires into the window.
- **OX time:** how action progresses across a turn.
- **Metacognition:** the layer above both axes that monitors and regulates.

Current engine markers:

- no agent framework
- generic `BotPolicy` interpreter
- pipeline runner default: ENGINE 0.7.0
- metacognition always on: ENGINE 0.7.1

## Core Idea

The engine decouples **what an assistant thinks about** from **how it
progresses**. Both are tuned independently, and a metacognition layer watches the
whole plane.

```text
metacognition: monitor -> regulate (always on)

OY lobe axis (context)
  ^
  | memory
  | task
  | skill
  | research
  | synthesize      each flow step consults a vertical lobe slice
  |
  +-------------------------------------------------> OX flow axis (time)
     plan -> research -> synthesize -> cite -> filter
```

| Axis | Meaning | Tuning Surface |
| --- | --- | --- |
| OY context | The vertical stack of context contributions in the window at any moment. | Flat `lobe weights`. Better context focus lives here. |
| OX time | The staged flow of action and decision across a turn. | The separate `flow_` namespace. Better sequencing lives here. |
| Metacognition | Snapshot observation and next-step regulation. | `monitor -> regulate`; never an LLM judging the pipeline. |

## Lobes: the context axis

A lobe is a small thinking unit. It organizes context and local behavior for a
slice of the turn, and deliberately does **not** decide the whole execution
plan.

The four-part lobe contract:

| Part | Role |
| --- | --- |
| `state` | Reactive gates deciding whether the lobe activates this turn. |
| `context` | Selected information for the prompt and the Blackboard. |
| `prompt` | Stable, slow, or volatile prompt blocks it contributes. |
| `behavior` | Optional executable work through the injected LLM/tool seam. |

Typical lobes:

- `memory_recall`
- `session_recall`
- `task_state`
- `skill_select`
- `skill_active`
- `research`
- `synthesize`
- `cite` (pinned)
- `filter` (pinned)
- `format`

Per-bot tuning uses flat weights to disable or bias a lobe's state nodes. These
weights stay separate from the `flow_` namespace so context tuning and flow
tuning do not leak into each other.

## Flows: the time axis

A flow is a complete named execution path. Each `FlowStep` owns its name, lobe
slice, loop mode, and tool allowlist. A new flow can drop in without rewriting
any lobe.

What a `FlowStep` owns:

| Field | Meaning |
| --- | --- |
| step name | `plan`, `research`, `cite`, and so on. |
| lobe slice | Which OY lobes the step consults. |
| loop mode | `none`, `single`, or `agentic`. |
| tool allowlist | Which tools are available to agentic steps. |

Tuning keys live under
`flow_...__step_...__lobe_...__add/remove`, which optimizes OX progression
without touching OY internals.

Default flows:

| Flow | Steps |
| --- | --- |
| `qna` | `synthesize` |
| `research` | `plan -> research -> synthesize -> cite -> filter` |
| `task_execute` | `advance -> format` |
| `clarify` | `synthesize` |
| `relational` | `synthesize` |

## Paths: the intent bridge

A path is the turn's recognized intent. It **biases lobes** on OY and **selects
the flow** on OX. Recognition runs every turn on free signals, lexical or
structural, and never uses an LLM.

```text
path recognizers
  score intent and bias lobes
        |
        v
Blackboard
  intent context node: {name, scores{...}}
        |
        v
flow pipeline
  reads intent and adapts steps
```

Path to flow is a pure data dependency through the Blackboard:

- `flows/` never imports paths.
- paths never import flows.
- recognition is produced once and consumed by both axes.
- the full score vector enables future multi-intent flow adaptation.
- lobes already blend every recognized path's bias.

## Skills: workflows the model is driven through

A skill is not a function call. It is a workflow. Two lobes run it, split by
state (each a self-describing `Lobe` with its own `state(ctx)`):

- **`skill_select`** (non-selected) — surface the index and steer selection:
  `skill:list -> skill.read:hint -> skill:select:cue`.
- **`skill_active`** (selected) — inject + drive the active skill:
  `skill:in_use -> skill:guide -> skill:context_vars`.

They coexist: list the rest while one drives. The `activated -> driving`
transition is reinforced at the exact `skill.read` moment.

```text
skill_select:  (none) -> listing -> selecting -> activating
skill_active:                                  -> activated -> driving
```

A skill can declare **`context_vars`** — custom per-skill workspace state
(`checklist`, `todos`, `notes`, `var`) that `skill_active` surfaces as pinned
context while the skill is driving and persists under `skill:<id>:<key>` via the
memory/tasks tools. The legacy `checklist` field is exposed as one
`type: checklist` var.

Multi-file skills navigate progressively via `skill.read {name, file, section}` /
`skill.search` — the body is the map; detail is read file-by-file, never
whole-bundle. Runtime records live state per skill in `trace.skills`
(`{slug: selecting | activated | driving}`).

New capability is a registry row, not an interpreter branch.

## Tasks: long-rail autonomous execution

A task fires on a schedule, runs across many continuation fires
(`max_continuations`), advances a `bot_task_todos` checklist, and must not lose
the thread between fires.

The engine handles this by writing its own working state to durable memory and
reading it back on later fires.

```text
recall_state
  -> advance
  -> checkpoint
  -> report
  -> continue?
       |
       v
conversation-scope memory
```

Steps round-trip through `context_entries` in conversation scope, isolated
server-side. This creates coherent self-memory across arbitrary fires with zero
raw-chunk leakage.

### Scratchpad Convention

The `task_execution` lobe emits a pinned `task_exec:scratchpad` node. The
`## Memory` block surfaces it back on the next fire.

```text
memory {
  save,
  scope: "conversation",
  key: "decision_* / var_* / intent",
  ttl_days: N
}
```

Seven long-running capabilities map to existing primitives:

| # | Capability | Primitive |
| --- | --- | --- |
| 1 | Self context-management | Scratchpad notes survive the window reset. |
| 2 | Long-rail plan / DSL | The todo checklist is the execution rail. |
| 3 | Per-step context awareness | Each rail step pulls only its lobe slice. |
| 4 | Error handling | Mark `blocked` or `failed`, note it, let meta retry or escalate. |
| 5 | Report progress | Emit a progress line per fire without ending the task. |
| 6 | Pause for human | `request_human` -> `waiting_for_human`. |
| 7 | Redirect on the fly | A reply or fact rewrites the rail: add, skip, or reorder todos. |

Task execution is a tuned flow, not a second interpreter.

## MCP: the internal tool boundary

The model sees one memory tool over four scopes. The isolation guarantee is
structural: the model picks a scope, and the harness resolves the `scope_ref`
from injected identity.

```text
memory {
  "action": save | forget | recall,
  "scope": bot | user | channel | conversation,
  "key": snake_case_id,
  "value": <any>,
  "ttl_days": 7
}
```

`save` upserts in place, `forget` hard-deletes, and `recall` reads one key or
lists visible entries only when a fact is truncated.

The MCP contract strips any caller-supplied identity, including `tenant_id`,
`bot_id`, `user_id`, `channel_id`, `conversation_id`, and `scope_ref`, before
the request is built. A turn can only read or write the refs for its own
identity.

Compression invariant: only memo-shaped notes cross the boundary, never raw
chunks.

## Context-aware Reasoning

An always-on `## Memory` index is injected every turn, so most recalls need zero
tool calls: the answer is already in the prompt. When budgets bite, an adaptive
builder scores what stays.

The four scopes are rendered broad to specific:

| Scope | Meaning |
| --- | --- |
| `bot` | Everyone, everywhere; admin-pinned. |
| `user` | One person, any channel. |
| `channel` | The common case: a team in a shared channel. |
| `conversation` | One thread only. |

They are rendered as `bot -> user -> channel -> conversation`, so the most
specific fact sits nearest the question and wins on conflict. Writes are
update-in-place: the same key overwrites, never a `_v2` near-duplicate.

Under `context_strategy in {adaptive, llm}`, the index returns post-cap
structured rows. Each memory node is scored, not dumped, using:

- lexical score
- semantic score
- budget fit
- per-scope boosts: `conversation > channel > user > bot`

Budgets:

- `INDEX_MAX_PER_SCOPE = 12`
- about a 1200-character value budget
- past the budget, entries degrade to hint-only with a "recall to read" note

Memory is not variables. Memory is what the bot chose to remember; context
variables are authoritative facts the engine recomputes every turn, never
invented or stale.

## Metacognition: thinking about thinking

Metacognition is always on since ENGINE 0.7.1. It has two jobs:

- **monitor:** observe the object-level engine.
- **regulate:** choose the next step.

It never lets an LLM judge the pipeline.

Modes:

| Mode | Meaning |
| --- | --- |
| `observe` | The floor. Monitor and trace every decision; never change execution. |
| `apply` | Monitor, trace, and apply the allow-listed regulation actions. |
| `METACOGNITION=observe` | Kill mutation, not monitoring. Legacy off-tokens degrade to shadow mode, never blindness. |

Observations:

- `context_tight`
- `empty_lobe_slice`
- `empty_step_context`
- `low_confidence_path`

Decisions:

- `continue`
- `adjust_lobe_slice`
- `retry_step`
- `skip_step`
- `meta_review`

Hard limits:

- Pinned steps `cite` and `filter` are never skippable. Ground-or-refuse is
  never a metacognition decision.
- `adjust_lobe_slice` only trims optional lobes. Pinned and step-defining lobes
  survive.

## Inspection and Optimization Surfaces

Read-only snapshot APIs:

| API | Snapshot |
| --- | --- |
| `inspect_lobe_axis` | Activation and context counts. |
| `inspect_flow_axis` | Steps, slices, and tools. |
| `snapshot_engine` | Whole-turn state. |
| `suggest_axis_optimizations` | Pure proposals. |

There are three tuning levels:

- lobe unit
- flow step
- whole engine

This is why lobe weights and `flow_` weights are kept deliberately separate.

## Source Docs

- [architecture.md](./architecture.md)
- [intent-and-paths.md](./intent-and-paths.md)
- [context-memory.md](./context-memory.md)
- [task-execution-mode.md](./task-execution-mode.md)
