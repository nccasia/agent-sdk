# Reasoning as a Tool

> Most tools act on the world. A **reasoning tool** acts on the agent itself — it reshapes what the
> agent can do and how it will think. `ActivateSkill` and the metacognition control tool are the same
> idea: the agent steers its own mind through the tool interface.

## The idea

An agent's tools split by *what they act on*:

- **World tools (effectors).** They reach outward — fetch information or cause external effects:
  `search`, `read_chunk`, an HTTP call, `fs.write`. Their result is *about the task*.
- **Reasoning tools (reflexive).** They reach *inward* — they change the agent's own cognitive state:
  which procedures it has loaded, what it's holding in mind, how it will proceed. Their result is *about
  the thinking*. `ActivateSkill` loads a procedure the agent didn't have a moment ago; a metacognition
  control tool re-picks the skills, the flow, or the subagents.

Both are the **same interface** — a `ToolRuntime` with `get_tool_specs()` + `call_tool()`
(`contracts/tools.py`). The model calls them identically. The only difference is the *target* of the
effect: outward at the environment, or inward at the agent's own thinking. So self-direction needs no
new machinery — the tool channel the agent already uses to act on the world is also how it acts on
itself.

This is the mental model under both [skills](./09-skills.md) and [metacognition](./11-metacognition.md):
**skills and metacognition are two families of reasoning tool.** A skill tool reshapes *what the agent
can do*; a metacognition tool reshapes *how the agent proceeds*. Same channel, different lever.

## Why route self-direction through tools

The model already knows how to use tools — it requests one, reads the result, and continues. Making
self-direction a tool gives it three properties for free, the same ones that make world tools work:

- **It's a first-class, declared action.** A reasoning tool has a name, a description, and a schema. The
  model *chooses* to reshape its thinking the way it chooses any action — visibly, not through a hidden
  side effect.
- **It's traceable.** Every reshaping is a tool call in the trace: *the agent decided to activate
  `refunds`, then re-scoped its tools*. You can see the mind change its own shape.
- **It's bounded and composable.** Tools are gated per stage (the allowlist), counted against budgets,
  and added/removed as capacity modules ([plugins](./10-plugins.md)). A reasoning tool inherits all of it.

The agent thinks by acting — and a reasoning tool is simply an action aimed at the machine that produces
its answers, rather than at the answer.

## The mechanism: reason → write → enact

A reasoning tool does **not** mutate the pipeline directly. It **writes a declared change into the
turn's cognitive state**; a deterministic enactor (a lobe) **reads and realizes it** on the next step.
This is the live pattern behind `ActivateSkill`:

```txt
   model calls ──▶ ActivateSkill("refunds")                       (reason — the LLM decides)
                        │
                        ▼ writes
   turn cognitive state:  lobe_outputs["skills_in_use"] += "refunds"   (skills/runtime.py:116 _mark_in_use)
                        │
                        ▼ reads + realizes, next step
   skill_active lobe ──▶ drives the loaded procedure into the prompt    (enact — deterministic)
```

`ActivateSkill`'s own result is the **reshaped surface** itself — the skill's instructions (or a table
of contents to read on demand), so the act of reshaping immediately hands the model its new capability.
The write surface is the turn's shared state ([shared-context](./07-shared-context.md)); the read+enact
surface is the deterministic lobe network. Tools reach that state through the `current_turn()` seam
(`engine.py:68`).

This split is what keeps self-direction safe: **the model decides through the tool; the deterministic
network enacts.** The object-level pipeline stays a pure function of `(spec, context)` — the
tool-written decision is simply part of `context` — so reasoning-as-a-tool never makes an LLM *silently*
judge the pipeline (the [metacognition](./11-metacognition.md) reconciliation, generalized).

## The families of reasoning tool

| Reasoning tool | Reshapes | Writes to | Enacted by | Status |
|---|---|---|---|---|
| `ActivateSkill(slug)` | capability — loads a procedure/SOP | `lobe_outputs["skills_in_use"]` | `skill_active` lobe | live (`skills/runtime.py`) |
| `skill.read` / `skill.search` | attention — pulls more of a procedure into view (progressive disclosure) | the read-back surface | the tool result itself | live |
| `memory` (scope=turn) | working set — offload / recall what's held in mind | the scratchpad / flash store | downstream steps recall it | live ([universal-memory](./06-universal-memory.md)) |
| metacognition control tool | process — re-pick skills / flow / subagents, trim / retry / skip | a meta-decision in turn state | the corresponding deterministic enactor | to build ([metacognition](./11-metacognition.md)) |
| subagent fan-out | delegation — spawn scoped sub-thinkers over a work-list | `scratchpad[fanout_key]` | `_map_stage` | live ([subagent-fanout](./12-subagent-fanout.md)) |

Every row is the same shape: a tool call that reshapes some axis of the agent's own cognition, written
as state, enacted deterministically. **Skills** are the capability family; **metacognition** is the
process family; both are reasoning-as-a-tool.

## A spectrum, not a wall

The world/reasoning split is a *gradient*, not two disjoint sets:

```txt
  pure world ───────────────────────────────────────────────▶ pure reasoning
  search · http · fs.write   memory(recall)   skill.read   ActivateSkill   metacognition-control
       acts on the world         holds in mind     pulls in        loads          reshapes the
                                                  knowledge      capability       whole process
```

`memory` and `skill.read` sit in the middle — they reach into a store (world-ish) but their *purpose* is
to change what the agent is thinking with (reasoning-ish). The point of the model is not to classify
each tool but to recognize that **the same interface spans both ends**, so an agent's capacity to think
about its own thinking is just more tools — declarable, composable, and removable like any other.

## Boundaries

- **Reason → write → enact, never reason → mutate.** A reasoning tool writes a declared change; the
  deterministic network enacts it. It does not reach in and rewire the pipeline mid-flight.
- **The object-level core stays deterministic.** Tool-written decisions enter as context; intent
  recognition, activation, attention/budget, and flow resolution remain pure functions
  (`CLAUDE.md` invariant 4).
- **Pinned guards are not reshapeable.** No reasoning tool can strip `cite` / `filter` —
  ground-or-refuse is never a tool decision (`PINNED_UNSKIPPABLE`).
- **Gated and budgeted like any tool.** Reasoning tools obey the per-stage allowlist and the turn's
  budgets; they are not a privileged side channel.
- **Same contract, no fork.** A reasoning tool is an ordinary `ToolRuntime` — no second tool API, no
  second interpreter.

## Implementation status

**Live.** The unifying contract (`ToolRuntime`, `contracts/tools.py`); the `current_turn()` write seam
(`engine.py:68`); the capability family (`ActivateSkill` / `skill.read` / `skill.search`,
`skills/runtime.py`, with `_mark_in_use` → `lobe_outputs["skills_in_use"]` → `skill_active`); the
working-set family (the `memory` tool at turn scope); delegation (`_map_stage` fan-out over
`scratchpad[fanout_key]`).

**To build.** The process family as first-class reasoning tools — the metacognition control tool + its
enactors for skills / flow / subagents ([metacognition](./11-metacognition.md)); a uniform trace facet that
tags a tool call as world vs reasoning so the inspector can show "the mind reshaping itself."

**Deferred.** A reasoning tool that defines *new* reasoning tools at runtime; cross-turn persistence of a
reshaping (a learned capability that survives the turn).

## Benchmarking

Live-only. The gate is behavioral: does exposing a reasoning tool make the agent reshape correctly —
activate the right skill, re-scope to the right flow, fan out when it should? This is the shape skillbench
already uses for `ActivateSkill` (did the right skill get activated, and did it lift the answer); the
same harness extends to each new reasoning tool.

## Related

- [Skills](./09-skills.md) — the capability family of reasoning tool (`ActivateSkill` + progressive disclosure).
- [Metacognition](./11-metacognition.md) — the process family; the control tool that reshapes skills/flow/subagents.
- [Subagent Fan-out](./12-subagent-fanout.md) — delegation as a reasoning tool (spawn scoped sub-thinkers).
- [Shared Context](./07-shared-context.md) — the turn state a reasoning tool writes into.
- [Tool Use at Scale](./05-tool-use-at-scale.md) — the result lifecycle a (world) tool's output flows through.
- [Universal Memory](./06-universal-memory.md) — the working-set store the `memory` tool reshapes.

## Design principle

A capable agent does not only reach out to act on the world — it reaches **in** to reshape how it
thinks, through the very same tool channel. Make self-direction a tool, and thinking about thinking
stops being a special layer and becomes just another action the agent can take.
